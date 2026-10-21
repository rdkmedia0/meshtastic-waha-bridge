# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.meshtastic_waha_bridge.waha import WahaClient, WahaNotFoundError, normalise_base_url

from .conftest import WAHA_URL

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def test_normalise_base_url() -> None:
    assert normalise_base_url(" http://waha.local:3000/ ") == "http://waha.local:3000"
    assert normalise_base_url("http://waha.local:3000/api") == "http://waha.local:3000"
    assert normalise_base_url("http://waha.local:3000/API/") == "http://waha.local:3000"
    assert normalise_base_url("https://waha.example.com/waha/api/") == "https://waha.example.com/waha"


async def test_session_lookup_falls_back_to_the_list(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/default", status=404)
    aioclient_mock.get(
        f"{WAHA_URL}/api/sessions",
        params={"all": "true"},
        json=[{"name": "other", "status": "STOPPED"}, {"name": "default", "status": "WORKING"}],
    )
    client = WahaClient(async_get_clientsession(hass), f"{WAHA_URL}/api", "key", "default")

    assert await client.async_get_session() == {"name": "default", "status": "WORKING"}

    client = WahaClient(async_get_clientsession(hass), WAHA_URL, "key", "missing")
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/missing", status=404)
    with pytest.raises(WahaNotFoundError):
        await client.async_get_session()


async def test_session_lookup_without_list_endpoint(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(f"{WAHA_URL}/api/sessions/default", status=404)
    aioclient_mock.get(f"{WAHA_URL}/api/sessions", params={"all": "true"}, status=404)
    client = WahaClient(async_get_clientsession(hass), WAHA_URL, None, "default")
    with pytest.raises(WahaNotFoundError):
        await client.async_get_session()
