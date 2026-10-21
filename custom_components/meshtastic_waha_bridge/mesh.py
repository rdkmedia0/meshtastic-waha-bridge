# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

"""Helpers for talking to the Meshtastic integration through its public actions and events."""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import (
    LONG_MESSAGES_SPLIT,
    MAX_CHUNKS,
    MAX_TEXT_BYTES,
    MESHTASTIC_DOMAIN,
    MESHTASTIC_SERVICE_SEND_TEXT,
    TARGET_CHANNEL,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

BROADCAST_NUM = 0xFFFFFFFF
_CHUNK_SUFFIX_BYTES = len(b" [9/9]")
_ELLIPSIS = "..."


@dataclass(frozen=True)
class MeshTarget:
    """A channel of the gateway or a single node."""

    kind: str
    channel_index: int | None = None
    node_id: int | None = None

    @property
    def is_channel(self) -> bool:
        return self.kind == TARGET_CHANNEL

    def describe(self) -> str:
        if self.is_channel:
            return f"channel {self.channel_index}"
        return f"node !{self.node_id:08x}"


def parse_node_id(value: str) -> int:
    """Accept a Meshtastic user id (``!a1b2c3d4``) or a node number."""
    value = value.strip()
    if value.startswith("!"):
        return int(value[1:], 16)
    return int(value)


def gateway_node_num(hass: HomeAssistant, gateway_entry_id: str) -> int | None:
    """Find the node number of the gateway behind a Meshtastic config entry."""
    entry = hass.config_entries.async_get_entry(gateway_entry_id)
    if entry is None:
        return None
    gateway_node = getattr(getattr(entry, "runtime_data", None), "gateway_node", None)
    if gateway_node is not None:
        with suppress(AttributeError, TypeError, ValueError):
            if (num := gateway_node.get("num")) is not None:
                return int(num)

    registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, gateway_entry_id):
        if device.via_device_id is not None:
            continue
        for domain, identifier in device.identifiers:
            if domain == MESHTASTIC_DOMAIN and str(identifier).isdigit():
                return int(identifier)
    return None


def node_display_name(hass: HomeAssistant, node_id: int, gateway_entry_id: str | None = None) -> str:
    """
    Return the long name of a node, falling back to its user id.

    Nodes added as devices are named by the device registry (so a name given in Home Assistant wins); every other
    node is looked up in the gateway's node database.
    """
    device = dr.async_get(hass).async_get_device(identifiers={(MESHTASTIC_DOMAIN, str(node_id))})
    if device is not None and (device.name_by_user or device.name):
        return device.name_by_user or device.name
    if gateway_entry_id is not None:
        entry = hass.config_entries.async_get_entry(gateway_entry_id)
        client = getattr(getattr(entry, "runtime_data", None), "client", None)
        get_node_info = getattr(client, "get_node_info", None)
        if callable(get_node_info):
            with suppress(Exception):
                node = get_node_info(node_id)
                if (name := getattr(node, "long_name", None)) or (name := getattr(node, "short_name", None)):
                    return str(name)
    return f"!{node_id:08x}"


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _cut(text: str, max_bytes: int) -> str:
    """Return the longest prefix of ``text`` that fits in ``max_bytes`` without breaking a character."""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def split_text(
    text: str,
    prefix: str = "",
    *,
    limit: int = MAX_TEXT_BYTES,
    mode: str = LONG_MESSAGES_SPLIT,
    max_chunks: int = MAX_CHUNKS,
) -> tuple[list[str], str]:
    """
    Fit a message into Meshtastic packets.

    Returns the packets to send (each at most ``limit`` bytes, prefix included) and the text that did not fit.
    """
    text = text.strip()
    budget = limit - _byte_len(prefix)
    if budget <= 0:
        msg = "The prefix alone exceeds the message limit"
        raise ValueError(msg)
    if not text:
        return [], ""
    if _byte_len(text) <= budget:
        return [prefix + text], ""
    if mode != LONG_MESSAGES_SPLIT:
        kept = _cut(text, budget - _byte_len(_ELLIPSIS)).rstrip()
        return [prefix + kept + _ELLIPSIS], text[len(kept) :].lstrip()

    per_chunk = budget - _CHUNK_SUFFIX_BYTES
    parts: list[str] = []
    remaining = text
    while remaining and len(parts) < max_chunks:
        if _byte_len(remaining) <= per_chunk:
            parts.append(remaining)
            remaining = ""
            break
        piece = _cut(remaining, per_chunk)
        space = piece.rfind(" ")
        if space > len(piece) // 2:
            piece = piece[:space]
        parts.append(piece.rstrip())
        remaining = remaining[len(piece) :].lstrip()
    count = len(parts)
    if count == 1:
        return [prefix + parts[0]], remaining
    return [f"{prefix}{part} [{i}/{count}]" for i, part in enumerate(parts, start=1)], remaining


def mesh_event_message(
    event_data: Mapping[str, Any], *, gateway_entry_id: str, gateway_num: int, target: MeshTarget
) -> tuple[int, str] | None:
    """Return (sender node, text) when a Meshtastic text event belongs to the bridged conversation."""
    if event_data.get("config_entry_id") != gateway_entry_id:
        return None
    data = event_data.get("data") or {}
    from_id = data.get("from")
    to = data.get("to") or {}
    text = data.get("message")
    if from_id is None or not text or from_id == gateway_num:
        return None
    if target.is_channel:
        if to.get("node") is not None or to.get("channel") != target.channel_index:
            return None
    elif to.get("node") != gateway_num or from_id != target.node_id:
        return None
    return int(from_id), str(text)


def chat_sent_event_message(
    event_data: Mapping[str, Any], *, gateway_entry_id: str, target: MeshTarget, own_prefix: str
) -> tuple[str | None, str] | None:
    """
    Return (sent_by label, text) when a message sent from Home Assistant belongs to the bridged conversation.

    Messages this bridge sent itself (their label starts with ``own_prefix``) are skipped so they are not mirrored
    back to the chat they came from.
    """
    if event_data.get("config_entry_id") != gateway_entry_id:
        return None
    to = event_data.get("to") or {}
    text = event_data.get("message")
    if not text:
        return None
    if target.is_channel:
        if to.get("channel") != target.channel_index:
            return None
    elif to.get("node") != target.node_id:
        return None
    sent_by = event_data.get("sent_by") or None
    if sent_by and sent_by.startswith(own_prefix):
        return None
    # the text is shown exactly as the mesh received it (including any sender prefix Mesh Chat added)
    return sent_by, str(text)


class MeshSender:
    """Sends text through the Meshtastic integration's ``send_text`` action."""

    def __init__(self, hass: HomeAssistant, gateway_entry_id: str) -> None:
        self._hass = hass
        self._gateway_entry_id = gateway_entry_id
        self.gateway_num: int | None = None
        self.supports_sent_by = False

    async def async_setup(self) -> None:
        if self._hass.config_entries.async_get_entry(self._gateway_entry_id) is None:
            msg = "The selected Meshtastic gateway no longer exists"
            raise ConfigEntryNotReady(msg)
        self.gateway_num = gateway_node_num(self._hass, self._gateway_entry_id)
        if self.gateway_num is None:
            msg = "The Meshtastic gateway is not loaded yet"
            raise ConfigEntryNotReady(msg)
        service = self._hass.services.async_services_for_domain(MESHTASTIC_DOMAIN).get(MESHTASTIC_SERVICE_SEND_TEXT)
        if service is None:
            msg = "The meshtastic.send_text action is not available yet"
            raise ConfigEntryNotReady(msg)
        self.supports_sent_by = _schema_accepts(service.schema, {"text": "probe", "sent_by": "probe"})
        if not self.supports_sent_by:
            _LOGGER.info(
                "The installed Meshtastic integration does not record messages in Mesh Chat (no sent_by support)"
            )

    async def async_send(self, target: MeshTarget, texts: list[str], *, sent_by: str | None = None) -> None:
        for text in texts:
            data: dict[str, Any] = {"from": str(self.gateway_num), "text": text, "ack": False}
            if target.is_channel:
                data["channel"] = str(target.channel_index)
            else:
                data["to"] = str(target.node_id)
            if sent_by and self.supports_sent_by:
                data["sent_by"] = sent_by
            try:
                await self._hass.services.async_call(
                    MESHTASTIC_DOMAIN, MESHTASTIC_SERVICE_SEND_TEXT, data, blocking=True
                )
            except HomeAssistantError:
                _LOGGER.warning("Failed to send message to the mesh (%s)", target.describe(), exc_info=True)
                return


def _schema_accepts(schema: Any, data: dict[str, Any]) -> bool:
    if schema is None:
        return False
    try:
        schema(data)
    except vol.Invalid:
        return False
    return True
