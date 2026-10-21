# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections import deque
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryError

from .const import (
    CONF_CHANNEL_INDEX,
    CONF_DIRECTION,
    CONF_GATEWAY_ENTRY_ID,
    CONF_HMAC_KEY,
    CONF_LONG_MESSAGES,
    CONF_NODE_ID,
    CONF_PREFIX_MESH_SENDER,
    CONF_PREFIX_WHATSAPP_SENDER,
    CONF_TARGET_TYPE,
    CONF_WEBHOOK_ID,
    CONF_WHATSAPP_CHAT_ID,
    DEDUPLICATE_WINDOW,
    DEFAULT_DIRECTION,
    DEFAULT_LONG_MESSAGES,
    DEFAULT_PREFIX_MESH_SENDER,
    DEFAULT_PREFIX_WHATSAPP_SENDER,
    DIRECTION_TO_MESH,
    DIRECTION_TO_WHATSAPP,
    DOMAIN,
    EVENT_MESHTASTIC_CHAT_MESSAGE_SENT,
    EVENT_MESHTASTIC_TEXT_MESSAGE,
    MAX_SENDER_NAME,
    MESH_SENDER_EMOJI,
    SENT_BY_PREFIX,
    TARGET_CHANNEL,
    WAHA_HMAC_HEADER,
    WAHA_MESSAGE_EVENTS,
)
from .mesh import (
    MeshSender,
    MeshTarget,
    chat_sent_event_message,
    mesh_event_message,
    node_display_name,
    parse_node_id,
    split_text,
)
from .waha import WahaError

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable, Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant

    from .waha import WahaClient

_LOGGER = logging.getLogger(__name__)


def target_from_entry(entry: ConfigEntry) -> MeshTarget:
    if entry.data[CONF_TARGET_TYPE] == TARGET_CHANNEL:
        return MeshTarget(TARGET_CHANNEL, channel_index=int(entry.data[CONF_CHANNEL_INDEX]))
    return MeshTarget(entry.data[CONF_TARGET_TYPE], node_id=parse_node_id(str(entry.data[CONF_NODE_ID])))


def sender_label(raw: str | None, fallback_id: str) -> str:
    """Return a short, single-line display name for a WhatsApp sender."""
    name = " ".join(str(raw or "").split())
    if not name:
        name = fallback_id.split("@", 1)[0] or "WhatsApp"
    return name[:MAX_SENDER_NAME].rstrip()


class WahaMeshBridge:
    """Forwards messages between one WhatsApp chat (through WAHA) and one Meshtastic channel or node."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: WahaClient) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.chat_id: str = entry.data[CONF_WHATSAPP_CHAT_ID]
        self.webhook_id: str = entry.data[CONF_WEBHOOK_ID]
        self.target = target_from_entry(entry)
        self.sender = MeshSender(hass, entry.data[CONF_GATEWAY_ENTRY_ID])
        self._hmac_key: str | None = entry.data.get(CONF_HMAC_KEY) or None
        self._unsubscribe: list[Callable[[], None]] = []
        self._webhook_registered = False
        self._seen: deque[Any] = deque(maxlen=DEDUPLICATE_WINDOW)
        self._names: dict[str, str] = {}
        # texts this bridge posted to WhatsApp, so they are not forwarded back when WAHA reports them as fromMe
        self._posted: deque[str] = deque(maxlen=DEDUPLICATE_WINDOW)
        # messages are forwarded one after the other, in the order they arrived
        self._outbox: deque[Callable[[], Awaitable[None]]] = deque()
        self._drain_task: asyncio.Task[None] | None = None

    @property
    def direction(self) -> str:
        return self.entry.options.get(CONF_DIRECTION, DEFAULT_DIRECTION)

    async def async_setup(self) -> None:
        await self.sender.async_setup()
        if self.direction != DIRECTION_TO_WHATSAPP:
            try:
                webhook.async_register(
                    self.hass,
                    DOMAIN,
                    f"Meshtastic WAHA bridge: {self.entry.title}",
                    self.webhook_id,
                    self.async_handle_webhook,
                    allowed_methods=["POST"],
                )
            except ValueError as err:
                msg = (
                    f"Webhook {self.webhook_id} is already used by another integration or an automation. "
                    "Disable that automation or reconfigure the bridge with a different webhook."
                )
                raise ConfigEntryError(msg) from err
            self._webhook_registered = True
        if self.direction != DIRECTION_TO_MESH:
            self._unsubscribe.append(self.hass.bus.async_listen(EVENT_MESHTASTIC_TEXT_MESSAGE, self._on_mesh_text))
            self._unsubscribe.append(
                self.hass.bus.async_listen(EVENT_MESHTASTIC_CHAT_MESSAGE_SENT, self._on_chat_message_sent)
            )

    async def async_unload(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        if self._webhook_registered:
            webhook.async_unregister(self.hass, self.webhook_id)
            self._webhook_registered = False
        self._outbox.clear()
        if self._drain_task is not None and not self._drain_task.done():
            self._drain_task.cancel()
        self._drain_task = None

    @callback
    def _enqueue(self, job: Callable[[], Awaitable[None]]) -> None:
        self._outbox.append(job)
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = self.hass.async_create_task(self._async_drain(), name=f"{self.entry.title} forward")

    async def _async_drain(self) -> None:
        while self._outbox:
            job = self._outbox.popleft()
            try:
                await job()
            except Exception:
                _LOGGER.exception("Unexpected error while forwarding a message")

    # ---------- WhatsApp -> mesh ----------

    async def async_handle_webhook(self, _hass: HomeAssistant, _webhook_id: str, request: web.Request) -> web.Response:
        body = await request.read()
        if self._hmac_key:
            expected = hmac.new(self._hmac_key.encode("utf-8"), body, hashlib.sha512).hexdigest()
            received = request.headers.get(WAHA_HMAC_HEADER, "").strip().lower()
            if not hmac.compare_digest(expected, received):
                _LOGGER.warning("Rejected WAHA webhook call with a missing or invalid signature")
                return web.Response(status=HTTPStatus.UNAUTHORIZED, text="invalid signature")
        try:
            event = json.loads(body)
        except ValueError:
            return web.Response(status=HTTPStatus.BAD_REQUEST, text="invalid json")
        if isinstance(event, dict):
            self._enqueue(lambda: self._async_process_waha_event(event))
        return web.Response(status=HTTPStatus.OK, text="ok")

    async def _async_process_waha_event(self, event: Mapping[str, Any]) -> None:  # noqa: PLR0911
        event_type = event.get("event")
        if event_type not in WAHA_MESSAGE_EVENTS:
            _LOGGER.debug("Ignoring WAHA event %s", event_type)
            return
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            return
        # for messages sent from the account's own phone WhatsApp puts the chat in "to" and the account in "from"
        chat = payload.get("to") if payload.get("fromMe") else payload.get("from")
        if self.chat_id not in (payload.get("from"), payload.get("to")):
            _LOGGER.debug("Ignoring message in chat %s, this bridge is for %s", chat, self.chat_id)
            return
        text = str(payload.get("body") or "").strip()
        if not text:
            _LOGGER.debug("Ignoring message without text (media, sticker, reaction ...)")
            return
        if payload.get("source") == "api":
            _LOGGER.debug("Ignoring message sent through the WAHA API: %s", text[:40])
            return
        if payload.get("fromMe") and text in self._posted:
            _LOGGER.debug("Ignoring message this bridge posted itself: %s", text[:40])
            return
        message_id = payload.get("id")
        if message_id is not None:
            if message_id in self._seen:
                _LOGGER.debug("Ignoring message %s reported twice", message_id)
                return
            self._seen.append(message_id)

        if payload.get("fromMe"):
            sender_id = str((event.get("me") or {}).get("id") or payload.get("to") or "")
            name = await self._async_sender_name({**payload, "_data": {}}, sender_id, me=event.get("me"))
        else:
            sender_id = str(payload.get("participant") or payload.get("from") or "")
            name = await self._async_sender_name(payload, sender_id)
        _LOGGER.debug("Forwarding message from %s in %s to the mesh", name, chat)
        prefix = (
            f"{name}: " if self.entry.options.get(CONF_PREFIX_WHATSAPP_SENDER, DEFAULT_PREFIX_WHATSAPP_SENDER) else ""
        )
        try:
            chunks, dropped = split_text(
                text, prefix, mode=self.entry.options.get(CONF_LONG_MESSAGES, DEFAULT_LONG_MESSAGES)
            )
        except ValueError:
            _LOGGER.warning("Cannot forward message from %s: the sender name alone exceeds the mesh limit", name)
            return
        if dropped:
            _LOGGER.warning(
                "Message from %s in WhatsApp chat %s was too long for the mesh, %d bytes were not sent",
                name,
                self.chat_id,
                len(dropped.encode("utf-8")),
            )
        await self.sender.async_send(self.target, chunks, sent_by=f"WhatsApp: {name}")

    async def _async_sender_name(
        self, payload: Mapping[str, Any], sender_id: str, *, me: Mapping[str, Any] | None = None
    ) -> str:
        engine_data = payload.get("_data") or {}
        raw = None
        if isinstance(engine_data, dict):
            raw = engine_data.get("notifyName") or engine_data.get("pushName") or engine_data.get("PushName")
        raw = raw or payload.get("pushName") or payload.get("notifyName") or (me or {}).get("pushName")
        if not raw and sender_id:
            if sender_id not in self._names:
                self._names[sender_id] = await self.client.async_contact_name(sender_id) or ""
            raw = self._names[sender_id]
        return sender_label(raw, sender_id)

    # ---------- mesh -> WhatsApp ----------

    @callback
    def _on_mesh_text(self, event: Event) -> None:
        if self.sender.gateway_num is None:
            return
        match = mesh_event_message(
            event.data,
            gateway_entry_id=self.entry.data[CONF_GATEWAY_ENTRY_ID],
            gateway_num=self.sender.gateway_num,
            target=self.target,
        )
        if match is None:
            return
        from_id, text = match
        packet_id = event.data.get("message_id")
        if packet_id is not None:
            key = (from_id, packet_id)
            if key in self._seen:
                return
            self._seen.append(key)

        if self.entry.options.get(CONF_PREFIX_MESH_SENDER, DEFAULT_PREFIX_MESH_SENDER):
            name = node_display_name(self.hass, from_id, self.entry.data[CONF_GATEWAY_ENTRY_ID])
            message = f"*{MESH_SENDER_EMOJI} {name}*: {text}"
        else:
            message = text
        self._enqueue(lambda: self._async_send_whatsapp(message))

    @callback
    def _on_chat_message_sent(self, event: Event) -> None:
        """Mirror messages sent from Home Assistant (the Mesh Chat panel, actions, other bridges)."""
        if self.sender.gateway_num is None:
            return
        match = chat_sent_event_message(
            event.data,
            gateway_entry_id=self.entry.data[CONF_GATEWAY_ENTRY_ID],
            target=self.target,
            own_prefix=SENT_BY_PREFIX,
        )
        if match is None:
            return
        key = ("sent", event.data.get("message_id"))
        if key in self._seen:
            return
        self._seen.append(key)
        sent_by, text = match
        label = sent_by or node_display_name(self.hass, self.sender.gateway_num, self.entry.data[CONF_GATEWAY_ENTRY_ID])
        if self.entry.options.get(CONF_PREFIX_MESH_SENDER, DEFAULT_PREFIX_MESH_SENDER):
            message = f"*{label}*: {text}"
        else:
            message = text
        self._enqueue(lambda: self._async_send_whatsapp(message))

    async def _async_send_whatsapp(self, message: str) -> None:
        self._posted.append(message)
        try:
            await self.client.async_send_text(self.chat_id, message)
        except WahaError as err:
            _LOGGER.warning("Failed to send message to WhatsApp chat %s: %s", self.chat_id, err)
