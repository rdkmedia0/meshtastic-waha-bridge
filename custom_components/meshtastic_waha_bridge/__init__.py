# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

"""Bridge a WhatsApp chat (through a self-hosted WAHA instance) and a Meshtastic channel or node."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bridge import WahaMeshBridge
from .const import CONF_API_KEY, CONF_SESSION, CONF_WAHA_URL, DEFAULT_SESSION
from .waha import WahaAuthError, WahaClient, WahaConnectionError, WahaError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

type WahaBridgeConfigEntry = ConfigEntry[WahaMeshBridge]


async def async_setup_entry(hass: HomeAssistant, entry: WahaBridgeConfigEntry) -> bool:
    client = WahaClient(
        async_get_clientsession(hass),
        entry.data[CONF_WAHA_URL],
        entry.data.get(CONF_API_KEY),
        entry.data.get(CONF_SESSION, DEFAULT_SESSION),
    )
    try:
        status = await client.async_get_session()
    except WahaAuthError as err:
        msg = "WAHA rejected the API key"
        raise ConfigEntryError(msg) from err
    except WahaConnectionError as err:
        msg = f"Cannot reach WAHA at {client.base_url}: {err}"
        raise ConfigEntryNotReady(msg) from err
    except WahaError as err:
        msg = f"WAHA session {client.waha_session!r} is not available: {err}"
        raise ConfigEntryNotReady(msg) from err
    if status.get("status") != "WORKING":
        _LOGGER.warning(
            "WAHA session %s is %s, messages cannot be sent to WhatsApp until it is working",
            client.waha_session,
            status.get("status", "unknown"),
        )

    bridge = WahaMeshBridge(hass, entry, client)
    await bridge.async_setup()
    entry.runtime_data = bridge
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WahaBridgeConfigEntry) -> bool:  # noqa: ARG001
    await entry.runtime_data.async_unload()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: WahaBridgeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
