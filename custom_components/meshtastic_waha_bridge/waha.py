# SPDX-FileCopyrightText: 2026 Meshtastic WAHA Bridge contributors
#
# SPDX-License-Identifier: MIT

"""Minimal client for the WAHA (WhatsApp HTTP API) REST interface."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import aiohttp

from .const import WAHA_TIMEOUT

if TYPE_CHECKING:
    from collections.abc import Mapping


class WahaError(Exception):
    """Base error of the WAHA client."""


class WahaConnectionError(WahaError):
    """WAHA could not be reached."""


class WahaAuthError(WahaError):
    """WAHA rejected the API key."""


class WahaNotFoundError(WahaError):
    """The session or chat does not exist."""


class WahaResponseError(WahaError):
    """WAHA answered with an error."""


def normalise_base_url(url: str) -> str:
    """Strip a trailing slash and a trailing ``/api``, which people copy from WAHA's dashboard URL."""
    url = url.strip().rstrip("/")
    if url.lower().endswith("/api"):
        url = url[:-4]
    return url.rstrip("/")


class WahaClient:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, api_key: str | None, waha_session: str) -> None:
        self._session = session
        self.base_url = normalise_base_url(base_url)
        self._api_key = api_key
        self.waha_session = waha_session

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers["X-Api-Key"] = self._api_key
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                params=params,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=WAHA_TIMEOUT),
            ) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise WahaAuthError
                if response.status == HTTPStatus.NOT_FOUND:
                    raise WahaNotFoundError
                if response.status >= HTTPStatus.BAD_REQUEST:
                    msg = f"WAHA answered {response.status}: {(await response.text())[:200]}"
                    raise WahaResponseError(msg)
                text = await response.text()
                try:
                    return json.loads(text) if text else None
                except ValueError:
                    return text
        except (aiohttp.ClientError, TimeoutError) as err:
            raise WahaConnectionError(str(err)) from err

    async def async_get_session(self) -> dict[str, Any]:
        """
        Return the state of the WhatsApp session, e.g. ``{"name": "default", "status": "WORKING"}``.

        Older WAHA versions have no single-session endpoint, so the session list is used as a fallback.
        """
        try:
            data = await self._request("GET", f"/api/sessions/{self.waha_session}")
        except WahaNotFoundError:
            sessions = await self._request("GET", "/api/sessions", params={"all": "true"})
            if not isinstance(sessions, list):
                raise
            for session in sessions:
                if isinstance(session, dict) and session.get("name") == self.waha_session:
                    return session
            raise
        return data if isinstance(data, dict) else {}

    async def async_send_text(self, chat_id: str, text: str) -> None:
        await self._request(
            "POST", "/api/sendText", payload={"session": self.waha_session, "chatId": chat_id, "text": text}
        )

    async def async_contact_name(self, contact_id: str) -> str | None:
        """Best effort lookup of a contact's name; returns None when WAHA does not know it."""
        try:
            data = await self._request(
                "GET", "/api/contacts", params={"contactId": contact_id, "session": self.waha_session}
            )
        except WahaError:
            return None
        if not isinstance(data, dict):
            return None
        name = str(data.get("name") or data.get("pushname") or data.get("pushName") or "").strip()
        return name or None
