"""Async client for the Basecamp 3/4 API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import aiohttp

from .const import API_BASE, USER_AGENT
from .stretches import OutOfOffice

MAX_ATTEMPTS = 5
DEFAULT_RETRY_AFTER = 10


class BasecampAuthError(Exception):
    """Basecamp rejected the token (HTTP 401)."""


class BasecampApiError(Exception):
    """Basecamp returned an error status, or the request failed."""


class BasecampClient:
    """Minimal Basecamp API client for out-of-office management."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_getter: Callable[[], Awaitable[str]],
        account_id: int,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._token_getter = token_getter
        self._base = API_BASE.format(account_id=account_id)

    async def get_person_id(self) -> int:
        """Return the authenticated user's Basecamp person ID."""
        data, _ = await self._request("GET", f"{self._base}/my/profile.json")
        return int(data["id"])

    async def list_schedule_entries(self, schedule_id: int) -> list[dict]:
        """Return all entries of a schedule, following pagination."""
        entries: list[dict] = []
        url: str | None = f"{self._base}/schedules/{schedule_id}/entries.json"
        while url is not None:
            page, url = await self._request("GET", url)
            entries.extend(page)
        return entries

    async def get_out_of_office(self, person_id: int) -> OutOfOffice:
        """Return the person's current out-of-office setting."""
        data, _ = await self._request("GET", self._ooo_url(person_id))
        return OutOfOffice.from_api(data)

    async def set_out_of_office(
        self, person_id: int, start: date, end: date
    ) -> OutOfOffice:
        """Set (replace) the person's out-of-office for start..end inclusive."""
        body = {
            "out_of_office": {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        }
        data, _ = await self._request("POST", self._ooo_url(person_id), json=body)
        return OutOfOffice.from_api(data)

    def _ooo_url(self, person_id: int) -> str:
        return f"{self._base}/people/{person_id}/out_of_office.json"

    async def _request(
        self, method: str, url: str, json: dict | None = None
    ) -> tuple[Any, str | None]:
        """Send a request; return (parsed JSON body, next-page URL or None).

        Retries 429 responses up to MAX_ATTEMPTS total, sleeping Retry-After
        seconds between attempts. Maps 401 to BasecampAuthError and any other
        non-2xx status or transport failure to BasecampApiError.
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            headers = {
                "Authorization": f"Bearer {await self._token_getter()}",
                "User-Agent": USER_AGENT,
            }
            try:
                async with self._session.request(
                    method, url, headers=headers, json=json
                ) as resp:
                    if resp.status == 429:
                        if attempt == MAX_ATTEMPTS:
                            raise BasecampApiError(
                                f"{method} {url}: still rate limited after "
                                f"{MAX_ATTEMPTS} attempts"
                            )
                        await asyncio.sleep(_retry_after(resp))
                        continue
                    if resp.status == 401:
                        raise BasecampAuthError(f"{method} {url}: unauthorized")
                    if not 200 <= resp.status < 300:
                        raise BasecampApiError(f"{method} {url}: HTTP {resp.status}")
                    data = await resp.json()
                    next_link = resp.links.get("next")
                    next_url = str(next_link["url"]) if next_link else None
                    return data, next_url
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise BasecampApiError(f"{method} {url}: {err}") from err
        raise AssertionError("unreachable")


def _retry_after(resp: aiohttp.ClientResponse) -> int:
    try:
        return max(0, int(resp.headers["Retry-After"]))
    except (KeyError, ValueError):
        return DEFAULT_RETRY_AFTER
