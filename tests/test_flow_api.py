# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.setup import async_setup_component

from custom_components.meshtastic_waha_bridge.const import DOMAIN

from .test_config_flow import CONNECTION, gateway_entry  # noqa: F401

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.usefixtures("waha")
async def test_forms_are_serialised_for_the_frontend(hass: HomeAssistant, hass_client) -> None:
    assert await async_setup_component(hass, "config", {})
    assert await async_setup_component(hass, "webhook", {})  # before the router freezes
    client = await hass_client()

    response = await client.post("/api/config/config_entries/flow", json={"handler": DOMAIN})
    assert response.status == 200, await response.text()
    data = await response.json()
    assert data["step_id"] == "user"
    assert [field["name"] for field in data["data_schema"]] == [
        "acknowledged",
        "waha_url",
        "api_key",
        "session",
        "hmac_key",
    ]

    response = await client.post(f"/api/config/config_entries/flow/{data['flow_id']}", json=CONNECTION)
    assert response.status == 200, await response.text()
    data = await response.json()
    assert data["step_id"] == "target"
    # the config_entry selector is not supported by the frontend in flows, only these are
    assert {next(iter(field["selector"])) for field in data["data_schema"]} <= {"select", "text", "number"}
    gateway = next(field for field in data["data_schema"] if field["name"] == "gateway_entry_id")
    assert gateway["selector"]["select"]["options"] == [{"value": "gateway-entry", "label": "Home Gateway"}]
