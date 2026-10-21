# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import hmac
import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.meshtastic_waha_bridge.bridge import sender_label
from custom_components.meshtastic_waha_bridge.const import (
    CONF_API_KEY,
    CONF_CHANNEL_INDEX,
    CONF_DIRECTION,
    CONF_GATEWAY_ENTRY_ID,
    CONF_HMAC_KEY,
    CONF_LONG_MESSAGES,
    CONF_NODE_ID,
    CONF_PREFIX_MESH_SENDER,
    CONF_PREFIX_WHATSAPP_SENDER,
    CONF_SESSION,
    CONF_TARGET_TYPE,
    CONF_WAHA_URL,
    CONF_WEBHOOK_ID,
    CONF_WHATSAPP_CHAT_ID,
    DIRECTION_TO_MESH,
    DIRECTION_TO_WHATSAPP,
    DOMAIN,
    LONG_MESSAGES_TRUNCATE,
    MAX_TEXT_BYTES,
)

from .conftest import ANNA, GATEWAY, GROUP, WAHA_URL

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

WEBHOOK_ID = "waha-test-webhook"
HMAC_KEY = "top-secret"


async def setup_bridge(hass: HomeAssistant, mesh: Any, **overrides: Any) -> MockConfigEntry:
    options = overrides.pop("options", {})
    data = {
        CONF_WAHA_URL: WAHA_URL,
        CONF_API_KEY: "key",
        CONF_SESSION: "default",
        CONF_HMAC_KEY: None,
        CONF_WEBHOOK_ID: WEBHOOK_ID,
        CONF_WHATSAPP_CHAT_ID: GROUP,
        CONF_GATEWAY_ENTRY_ID: mesh.gateway.entry_id,
        CONF_TARGET_TYPE: "channel",
        CONF_CHANNEL_INDEX: 1,
        CONF_NODE_ID: None,
        **overrides,
    }
    entry = MockConfigEntry(domain=DOMAIN, title="bridge", data=data, options=options)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def waha_message(  # noqa: PLR0913
    text: str,
    *,
    chat: str = GROUP,
    participant: str | None = "4915112345678@c.us",
    name: str | None = "Alice",
    message_id: str = "false_x_1",
    from_me: bool = False,
    event: str = "message",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": message_id,
        "timestamp": 1700000000,
        "from": chat,
        "fromMe": from_me,
        "to": "4915100000000@c.us",
        "body": text,
        "hasMedia": False,
        "_data": {"notifyName": name} if name else {},
    }
    if participant:
        payload["participant"] = participant
    return {"event": event, "session": "default", "payload": payload}


async def post_webhook(client: Any, event: dict[str, Any], *, key: str | None = None) -> int:
    body = json.dumps(event).encode()
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["X-Webhook-Hmac"] = hmac.new(key.encode(), body, hashlib.sha512).hexdigest()
    response = await client.post(f"/api/webhook/{WEBHOOK_ID}", data=body, headers=headers)
    return response.status


def fire_mesh(  # noqa: PLR0913
    hass: HomeAssistant,
    entry_id: str,
    *,
    text: str = "hi",
    from_id: int = ANNA,
    to_node: int | None = None,
    channel: int | None = 1,
    message_id: int = 1,
) -> None:
    hass.bus.async_fire(
        "meshtastic_api_text_message",
        {
            "config_entry_id": entry_id,
            "node": from_id,
            "data": {"from": from_id, "to": {"node": to_node, "channel": channel}, "gateway": GATEWAY, "message": text},
            "message_id": message_id,
        },
    )


def sent_to_whatsapp(waha: Any) -> list[dict[str, Any]]:
    return [call[2] for call in waha.mock_calls if call[0] == "POST" and str(call[1]).endswith("/api/sendText")]


@pytest.mark.usefixtures("waha")
async def test_whatsapp_to_mesh(hass: HomeAssistant, mesh, hass_client_no_auth) -> None:
    entry = await setup_bridge(hass, mesh)
    assert entry.state is ConfigEntryState.LOADED
    client = await hass_client_no_auth()

    assert await post_webhook(client, waha_message("  on my way ")) == HTTPStatus.OK
    assert await post_webhook(client, waha_message("on my way")) == HTTPStatus.OK  # WAHA retried the same message
    assert await post_webhook(client, waha_message("other chat", chat="1@g.us", message_id="2")) == HTTPStatus.OK
    assert await post_webhook(client, waha_message("", message_id="4")) == HTTPStatus.OK  # media only
    assert await post_webhook(client, waha_message("ack", event="message.ack", message_id="5")) == HTTPStatus.OK
    api_sent = waha_message("via api", from_me=True, message_id="6", event="message.any")
    api_sent["payload"]["source"] = "api"
    assert await post_webhook(client, api_sent) == HTTPStatus.OK
    # a message typed on the phone WAHA is logged in as
    mine = waha_message("mine from my phone", from_me=True, name=None, participant=None, message_id="7")
    mine["me"] = {"id": "4915100000000@c.us", "pushName": "Steve"}
    assert await post_webhook(client, mine) == HTTPStatus.OK
    await hass.async_block_till_done()

    assert mesh.sent == [
        {"from": str(GATEWAY), "channel": "1", "text": "Alice: on my way", "ack": False, "sent_by": "WhatsApp: Alice"},
        {
            "from": str(GATEWAY),
            "channel": "1",
            "text": "Steve: mine from my phone",
            "ack": False,
            "sent_by": "WhatsApp: Steve",
        },
    ]

    assert await hass.config_entries.async_unload(entry.entry_id)
    response = await client.post(f"/api/webhook/{WEBHOOK_ID}", json=waha_message("after unload", message_id="6"))
    assert response.status == HTTPStatus.OK  # unknown webhooks are accepted silently by Home Assistant
    await hass.async_block_till_done()
    assert len(mesh.sent) == 2


async def test_webhook_signature(hass: HomeAssistant, mesh, waha, hass_client_no_auth) -> None:
    await setup_bridge(hass, mesh, **{CONF_HMAC_KEY: HMAC_KEY})
    client = await hass_client_no_auth()

    assert await post_webhook(client, waha_message("unsigned")) == HTTPStatus.UNAUTHORIZED
    assert await post_webhook(client, waha_message("wrong key"), key="nope") == HTTPStatus.UNAUTHORIZED
    assert await post_webhook(client, waha_message("signed"), key=HMAC_KEY) == HTTPStatus.OK
    response = await client.post(
        f"/api/webhook/{WEBHOOK_ID}",
        data=b"not json",
        headers={"X-Webhook-Hmac": hmac.new(HMAC_KEY.encode(), b"not json", hashlib.sha512).hexdigest()},
    )
    assert response.status == HTTPStatus.BAD_REQUEST
    await hass.async_block_till_done()

    assert [m["text"] for m in mesh.sent] == ["Alice: signed"]
    assert sent_to_whatsapp(waha) == []


async def test_sender_name_lookup(hass: HomeAssistant, mesh, waha, hass_client_no_auth) -> None:
    waha.get(
        f"{WAHA_URL}/api/contacts",
        params={"contactId": "4915112345678@c.us", "session": "default"},
        json={"id": "4915112345678@c.us", "name": "Bob Builder", "pushname": "Bobby"},
    )
    waha.get(f"{WAHA_URL}/api/contacts", params={"contactId": "4915199999999@c.us", "session": "default"}, status=404)
    await setup_bridge(hass, mesh, options={CONF_LONG_MESSAGES: LONG_MESSAGES_TRUNCATE})
    client = await hass_client_no_auth()

    await post_webhook(client, waha_message("hello", name=None, message_id="1"))
    await post_webhook(client, waha_message("again", name=None, message_id="2"))  # cached, no second lookup
    await post_webhook(client, waha_message("unknown", name=None, participant="4915199999999@c.us", message_id="3"))
    await post_webhook(client, waha_message("x" * 300, name="A very long WhatsApp display name", message_id="4"))
    await hass.async_block_till_done()

    assert [m["text"] for m in mesh.sent[:3]] == ["Bob Builder: hello", "Bob Builder: again", "4915199999999: unknown"]
    assert mesh.sent[3]["text"].startswith("A very long WhatsApp dis: xxx")
    assert mesh.sent[3]["text"].endswith("...")
    assert len(mesh.sent[3]["text"].encode()) <= MAX_TEXT_BYTES
    lookups = [c for c in waha.mock_calls if c[0] == "GET" and str(c[1]).startswith(f"{WAHA_URL}/api/contacts")]
    assert len(lookups) == 2


async def test_mesh_to_whatsapp(hass: HomeAssistant, mesh, waha, hass_client_no_auth) -> None:
    await setup_bridge(hass, mesh)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=mesh.gateway.entry_id, identifiers={("meshtastic", str(ANNA))}, name="Anna Hiking"
    )

    fire_mesh(hass, mesh.gateway.entry_id, text="hello", message_id=10)
    fire_mesh(hass, mesh.gateway.entry_id, text="hello", message_id=10)  # reported twice by the gateway
    fire_mesh(hass, mesh.gateway.entry_id, text="other channel", channel=2, message_id=11)
    fire_mesh(hass, mesh.gateway.entry_id, text="from the gateway", from_id=GATEWAY, message_id=12)
    fire_mesh(hass, "other-gateway", text="other gateway", message_id=13)
    await hass.async_block_till_done()

    assert sent_to_whatsapp(waha) == [{"session": "default", "chatId": GROUP, "text": "*Anna Hiking*: hello"}]
    headers = next(call[3] for call in waha.mock_calls if call[0] == "POST")
    assert headers["X-Api-Key"] == "key"

    # WAHA reports the message the bridge just posted as fromMe: it must not be forwarded back to the mesh
    client = await hass_client_no_auth()
    echo = waha_message("*Anna Hiking*: hello", from_me=True, name=None, participant=None, message_id="echo")
    assert await post_webhook(client, echo) == HTTPStatus.OK
    await hass.async_block_till_done()
    assert mesh.sent == []


async def test_mesh_to_whatsapp_failure_is_logged(hass: HomeAssistant, mesh, aioclient_mock, caplog) -> None:
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/default", json={"name": "default", "status": "SCAN_QR_CODE"})
    aioclient_mock.post(f"{WAHA_URL}/api/sendText", status=500, text="boom")
    entry = await setup_bridge(hass, mesh, options={CONF_PREFIX_MESH_SENDER: False})
    assert entry.state is ConfigEntryState.LOADED
    assert "SCAN_QR_CODE" in caplog.text

    fire_mesh(hass, mesh.gateway.entry_id, text="plain")
    await hass.async_block_till_done()
    assert "Failed to send message to WhatsApp chat" in caplog.text


async def test_node_target_and_direction(hass: HomeAssistant, mesh, waha, hass_client_no_auth) -> None:
    await setup_bridge(hass, mesh, **{CONF_TARGET_TYPE: "node", CONF_NODE_ID: str(ANNA)})
    client = await hass_client_no_auth()

    await post_webhook(client, waha_message("ping"))
    fire_mesh(hass, mesh.gateway.entry_id, text="pong", to_node=GATEWAY, channel=None, message_id=1)
    fire_mesh(hass, mesh.gateway.entry_id, text="on a channel", message_id=2)
    await hass.async_block_till_done()

    assert mesh.sent == [
        {"from": str(GATEWAY), "to": str(ANNA), "text": "Alice: ping", "ack": False, "sent_by": "WhatsApp: Alice"}
    ]
    assert [m["text"] for m in sent_to_whatsapp(waha)] == ["*!a1b2c3d4*: pong"]


async def test_direction_options(hass: HomeAssistant, mesh, waha, hass_client_no_auth) -> None:
    entry = await setup_bridge(hass, mesh, options={CONF_DIRECTION: DIRECTION_TO_WHATSAPP})
    client = await hass_client_no_auth()

    await post_webhook(client, waha_message("ping"))
    fire_mesh(hass, mesh.gateway.entry_id, text="pong")
    await hass.async_block_till_done()
    assert mesh.sent == []
    assert len(sent_to_whatsapp(waha)) == 1

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_DIRECTION: DIRECTION_TO_MESH,
            CONF_PREFIX_WHATSAPP_SENDER: False,
            CONF_PREFIX_MESH_SENDER: True,
            CONF_LONG_MESSAGES: LONG_MESSAGES_TRUNCATE,
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    await post_webhook(client, waha_message("ping", message_id="2"))
    fire_mesh(hass, mesh.gateway.entry_id, text="pong", message_id=2)
    await hass.async_block_till_done()
    assert [m["text"] for m in mesh.sent] == ["ping"]
    assert len(sent_to_whatsapp(waha)) == 1


async def test_setup_errors(hass: HomeAssistant, mesh, aioclient_mock) -> None:
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/default", status=401)
    entry = await setup_bridge(hass, mesh)
    assert entry.state is ConfigEntryState.SETUP_ERROR

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/default", exc=TimeoutError)
    entry = await setup_bridge(hass, mesh, **{CONF_WHATSAPP_CHAT_ID: "2@g.us"})
    assert entry.state is ConfigEntryState.SETUP_RETRY

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/default", json={"status": "WORKING"})
    entry = await setup_bridge(hass, mesh, **{CONF_WHATSAPP_CHAT_ID: "3@g.us", CONF_GATEWAY_ENTRY_ID: "missing"})
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.usefixtures("waha")
async def test_webhook_already_in_use(hass: HomeAssistant, mesh) -> None:
    await async_setup_component(hass, "webhook", {})
    webhook.async_register(hass, "other", "other", WEBHOOK_ID, lambda *_: None)
    entry = await setup_bridge(hass, mesh)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "already used" in str(entry.reason)


def test_sender_label() -> None:
    assert sender_label("  Alice\n Smith ", "1@c.us") == "Alice Smith"
    assert sender_label(None, "4915112345678@c.us") == "4915112345678"
    assert sender_label("", "") == "WhatsApp"
    assert sender_label("x" * 40, "1@c.us") == "x" * 24


def fire_chat_sent(  # noqa: PLR0913
    hass: HomeAssistant,
    entry_id: str,
    *,
    text: str = "from the panel",
    sent_by: str | None = "Steve Smith",
    channel: int | None = 1,
    to_node: int | None = None,
    message_id: str = "m1",
) -> None:
    hass.bus.async_fire(
        "meshtastic_chat_message_sent",
        {
            "config_entry_id": entry_id,
            "gateway": GATEWAY,
            "message_id": message_id,
            "to": {"node": to_node, "channel": channel},
            "message": text,
            "sent_by": sent_by,
            "status": "sent",
        },
    )


async def test_messages_sent_from_home_assistant_are_mirrored(hass: HomeAssistant, mesh, waha) -> None:
    entry = await setup_bridge(hass, mesh)
    gateway = mesh.gateway.entry_id

    fire_chat_sent(hass, gateway, message_id="1")  # from the Mesh Chat panel
    fire_chat_sent(hass, gateway, message_id="1")  # duplicate
    fire_chat_sent(hass, gateway, text="Bob: hi", sent_by="WhatsApp: Bob", message_id="2")  # our own, skipped
    fire_chat_sent(hass, gateway, text="Alice: hi", sent_by="Telegram: Alice", message_id="3")  # another bridge
    fire_chat_sent(hass, gateway, text="automation", sent_by=None, message_id="4")
    fire_chat_sent(hass, gateway, text="other channel", channel=2, message_id="5")
    fire_chat_sent(hass, "other-gateway", text="other gateway", message_id="6")
    await hass.async_block_till_done()

    assert [m["text"] for m in sent_to_whatsapp(waha)] == [
        "*Steve Smith*: from the panel",
        "*Telegram: Alice*: Alice: hi",
        "*!00004904*: automation",
    ]
    assert mesh.sent == []

    assert await hass.config_entries.async_unload(entry.entry_id)
