# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError

from .const import (
    CONF_ACKNOWLEDGED,
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
    DEFAULT_DIRECTION,
    DEFAULT_LONG_MESSAGES,
    DEFAULT_PREFIX_MESH_SENDER,
    DEFAULT_PREFIX_WHATSAPP_SENDER,
    DEFAULT_SESSION,
    DIRECTION_BOTH,
    DIRECTION_TO_MESH,
    DIRECTION_TO_WHATSAPP,
    DOMAIN,
    LONG_MESSAGES_SPLIT,
    LONG_MESSAGES_TRUNCATE,
    MESHTASTIC_DOMAIN,
    TARGET_CHANNEL,
    TARGET_NODE,
)
from .mesh import parse_node_id
from .waha import WahaAuthError, WahaClient, WahaConnectionError, WahaError, WahaNotFoundError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
    from homeassistant.core import HomeAssistant

MAX_CHANNEL_INDEX = 7


def webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Return the URL WAHA has to call, or just the path when Home Assistant has no URL configured."""
    try:
        return webhook.async_generate_url(hass, webhook_id)
    except NoURLAvailableError:
        return webhook.async_generate_path(webhook_id)


def _user_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ACKNOWLEDGED, default=defaults.get(CONF_ACKNOWLEDGED, False)): selector.BooleanSelector(),
            vol.Required(CONF_WAHA_URL, default=defaults.get(CONF_WAHA_URL, vol.UNDEFINED)): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
            ),
            vol.Optional(CONF_API_KEY, default=defaults.get(CONF_API_KEY, vol.UNDEFINED)): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_SESSION, default=defaults.get(CONF_SESSION, DEFAULT_SESSION)): selector.TextSelector(),
            vol.Optional(CONF_HMAC_KEY, default=defaults.get(CONF_HMAC_KEY, vol.UNDEFINED)): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        }
    )


def _gateway_selector(hass: HomeAssistant) -> selector.SelectSelector:
    """Dropdown of the Meshtastic gateways (the config_entry selector cannot be used in flows)."""
    options = [
        selector.SelectOptionDict(value=entry.entry_id, label=entry.title)
        for entry in hass.config_entries.async_entries(MESHTASTIC_DOMAIN)
    ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
    )


def _target_schema(hass: HomeAssistant, defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WHATSAPP_CHAT_ID, default=defaults.get(CONF_WHATSAPP_CHAT_ID, vol.UNDEFINED)): (
                selector.TextSelector()
            ),
            vol.Required(CONF_GATEWAY_ENTRY_ID, default=defaults.get(CONF_GATEWAY_ENTRY_ID, vol.UNDEFINED)): (
                _gateway_selector(hass)
            ),
            vol.Required(CONF_TARGET_TYPE, default=defaults.get(CONF_TARGET_TYPE, TARGET_CHANNEL)): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[TARGET_CHANNEL, TARGET_NODE],
                        translation_key=CONF_TARGET_TYPE,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            ),
            vol.Optional(CONF_CHANNEL_INDEX, default=defaults.get(CONF_CHANNEL_INDEX, 0)): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=MAX_CHANNEL_INDEX, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(CONF_NODE_ID, default=defaults.get(CONF_NODE_ID, vol.UNDEFINED)): selector.TextSelector(),
        }
    )


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_DIRECTION, default=options.get(CONF_DIRECTION, DEFAULT_DIRECTION)): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[DIRECTION_BOTH, DIRECTION_TO_MESH, DIRECTION_TO_WHATSAPP],
                        translation_key=CONF_DIRECTION,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            ),
            vol.Required(
                CONF_PREFIX_WHATSAPP_SENDER,
                default=options.get(CONF_PREFIX_WHATSAPP_SENDER, DEFAULT_PREFIX_WHATSAPP_SENDER),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_PREFIX_MESH_SENDER, default=options.get(CONF_PREFIX_MESH_SENDER, DEFAULT_PREFIX_MESH_SENDER)
            ): selector.BooleanSelector(),
            vol.Required(CONF_LONG_MESSAGES, default=options.get(CONF_LONG_MESSAGES, DEFAULT_LONG_MESSAGES)): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[LONG_MESSAGES_SPLIT, LONG_MESSAGES_TRUNCATE],
                        translation_key=CONF_LONG_MESSAGES,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            ),
        }
    )


def normalise_chat_id(value: str) -> str:
    """Accept ``123@g.us``, ``4915…@c.us`` or a bare phone number (which becomes a ``@c.us`` chat)."""
    value = value.strip().replace(" ", "")
    if "@" in value:
        return value
    digits = value.lstrip("+")
    if not digits.isdigit():
        msg = "not a chat id"
        raise ValueError(msg)
    return f"{digits}@c.us"


def validate_target(user_input: dict[str, Any], errors: dict[str, str]) -> dict[str, Any]:
    """Normalise the user input of the target step, collecting errors per field."""
    data: dict[str, Any] = {
        CONF_GATEWAY_ENTRY_ID: user_input[CONF_GATEWAY_ENTRY_ID],
        CONF_TARGET_TYPE: user_input[CONF_TARGET_TYPE],
    }
    try:
        data[CONF_WHATSAPP_CHAT_ID] = normalise_chat_id(str(user_input[CONF_WHATSAPP_CHAT_ID]))
    except ValueError:
        errors[CONF_WHATSAPP_CHAT_ID] = "invalid_chat_id"

    if user_input[CONF_TARGET_TYPE] == TARGET_CHANNEL:
        data[CONF_CHANNEL_INDEX] = int(user_input.get(CONF_CHANNEL_INDEX, 0))
    else:
        node_id = str(user_input.get(CONF_NODE_ID) or "").strip()
        if not node_id:
            errors[CONF_NODE_ID] = "node_id_required"
        else:
            try:
                data[CONF_NODE_ID] = f"!{parse_node_id(node_id):08x}"
            except ValueError:
                errors[CONF_NODE_ID] = "invalid_node_id"
    return data


def _target_label(data: dict[str, Any]) -> str:
    if data[CONF_TARGET_TYPE] == TARGET_CHANNEL:
        return f"channel {data[CONF_CHANNEL_INDEX]}"
    return f"node {data[CONF_NODE_ID]}"


class MeshtasticWahaBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._connection: dict[str, Any] = {}
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if not self.hass.config_entries.async_entries(MESHTASTIC_DOMAIN):
            return self.async_abort(reason="no_gateway")
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_ACKNOWLEDGED):
                errors[CONF_ACKNOWLEDGED] = "must_acknowledge"
            else:
                client = WahaClient(
                    async_get_clientsession(self.hass),
                    user_input[CONF_WAHA_URL],
                    user_input.get(CONF_API_KEY) or None,
                    user_input.get(CONF_SESSION) or DEFAULT_SESSION,
                )
                try:
                    await client.async_get_session()
                except WahaAuthError:
                    errors["base"] = "invalid_auth"
                except WahaNotFoundError:
                    errors[CONF_SESSION] = "session_not_found"
                except WahaConnectionError:
                    errors["base"] = "cannot_connect"
                except WahaError:
                    errors["base"] = "unknown"
            if not errors:
                self._connection = {
                    CONF_WAHA_URL: client.base_url,
                    CONF_API_KEY: user_input.get(CONF_API_KEY) or None,
                    CONF_SESSION: client.waha_session,
                    CONF_HMAC_KEY: user_input.get(CONF_HMAC_KEY) or None,
                }
                return await self.async_step_target()
        return self.async_show_form(step_id="user", data_schema=_user_schema(user_input or {}), errors=errors)

    async def async_step_target(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = validate_target(user_input, errors)
            if not errors:
                unique_id = "|".join(
                    (self._connection[CONF_WAHA_URL], self._connection[CONF_SESSION], data[CONF_WHATSAPP_CHAT_ID])
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                self._data = {**self._connection, **data, CONF_WEBHOOK_ID: webhook.async_generate_id()}
                return await self.async_step_webhook()
        return self.async_show_form(
            step_id="target", data_schema=_target_schema(self.hass, user_input or {}), errors=errors
        )

    async def async_step_webhook(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            title = f"WhatsApp {self._data[CONF_WHATSAPP_CHAT_ID]} and {_target_label(self._data)}"
            return self.async_create_entry(title=title, data=self._data, options={})
        return self.async_show_form(
            step_id="webhook",
            data_schema=vol.Schema({}),
            description_placeholders={"webhook_url": webhook_url(self.hass, self._data[CONF_WEBHOOK_ID])},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:  # noqa: ARG004
        return MeshtasticWahaBridgeOptionsFlow()


class MeshtasticWahaBridgeOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
            description_placeholders={"webhook_url": webhook_url(self.hass, self.config_entry.data[CONF_WEBHOOK_ID])},
        )
