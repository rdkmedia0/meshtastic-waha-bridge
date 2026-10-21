# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.meshtastic_waha_bridge.config_flow import normalise_chat_id
from custom_components.meshtastic_waha_bridge.const import (
    CONF_ACKNOWLEDGED,
    CONF_API_KEY,
    CONF_CHANNEL_INDEX,
    CONF_GATEWAY_ENTRY_ID,
    CONF_HMAC_KEY,
    CONF_NODE_ID,
    CONF_SESSION,
    CONF_TARGET_TYPE,
    CONF_WAHA_URL,
    CONF_WEBHOOK_ID,
    CONF_WHATSAPP_CHAT_ID,
    DOMAIN,
)

from .conftest import GROUP, WAHA_URL

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture(autouse=True)
def no_setup():
    with patch("custom_components.meshtastic_waha_bridge.async_setup_entry", AsyncMock(return_value=True)):
        yield


CONNECTION = {
    CONF_ACKNOWLEDGED: True,
    CONF_WAHA_URL: f"{WAHA_URL}/",
    CONF_API_KEY: "key",
    CONF_SESSION: "default",
    CONF_HMAC_KEY: "sign",
}


@pytest.mark.usefixtures("waha")
async def test_full_flow(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={**CONNECTION, CONF_ACKNOWLEDGED: False}
    )
    assert result["errors"] == {CONF_ACKNOWLEDGED: "must_acknowledge"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=CONNECTION)
    assert result["type"] == "form"
    assert result["step_id"] == "target"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_WHATSAPP_CHAT_ID: f" {GROUP} ",
            CONF_GATEWAY_ENTRY_ID: "gateway-entry",
            CONF_TARGET_TYPE: "channel",
            CONF_CHANNEL_INDEX: 2.0,
        },
    )
    assert result["type"] == "form"
    assert result["step_id"] == "webhook"
    assert "/api/webhook/" in result["description_placeholders"]["webhook_url"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert result["type"] == "create_entry"
    assert result["title"] == f"WhatsApp {GROUP} and channel 2"
    data = result["data"]
    assert data[CONF_WEBHOOK_ID]
    del data[CONF_WEBHOOK_ID]
    assert data == {
        CONF_WAHA_URL: WAHA_URL,
        CONF_API_KEY: "key",
        CONF_SESSION: "default",
        CONF_HMAC_KEY: "sign",
        CONF_WHATSAPP_CHAT_ID: GROUP,
        CONF_GATEWAY_ENTRY_ID: "gateway-entry",
        CONF_TARGET_TYPE: "channel",
        CONF_CHANNEL_INDEX: 2,
    }

    # the same chat cannot be bridged twice through the same session
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=CONNECTION)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_WHATSAPP_CHAT_ID: GROUP, CONF_GATEWAY_ENTRY_ID: "x", CONF_TARGET_TYPE: "channel"},
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, {"base": "invalid_auth"}), (404, {CONF_SESSION: "session_not_found"}), (500, {"base": "unknown"})],
)
async def test_connection_errors(hass: HomeAssistant, aioclient_mock, status, expected) -> None:
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/default", status=status, text="nope")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=CONNECTION)
    assert result["type"] == "form"
    assert result["errors"] == expected


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/default", exc=TimeoutError)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=CONNECTION)
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.usefixtures("waha")
async def test_target_errors(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=CONNECTION)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_WHATSAPP_CHAT_ID: "not a chat", CONF_GATEWAY_ENTRY_ID: "g", CONF_TARGET_TYPE: "node"},
    )
    assert result["errors"] == {CONF_WHATSAPP_CHAT_ID: "invalid_chat_id", CONF_NODE_ID: "node_id_required"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            CONF_WHATSAPP_CHAT_ID: "+49 151 12345678",
            CONF_GATEWAY_ENTRY_ID: "g",
            CONF_TARGET_TYPE: "node",
            CONF_NODE_ID: "!a1b2c3d4",
        },
    )
    assert result["step_id"] == "webhook"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert result["title"] == "WhatsApp 4915112345678@c.us and node !a1b2c3d4"


def test_normalise_chat_id() -> None:
    assert normalise_chat_id(" 1@g.us ") == "1@g.us"
    assert normalise_chat_id("+49 151 1") == "491511@c.us"
    with pytest.raises(ValueError, match="chat id"):
        normalise_chat_id("abc")
