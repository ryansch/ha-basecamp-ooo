"""Tests for the Basecamp API client."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from aioresponses import aioresponses
import pytest
from yarl import URL

from custom_components.basecamp_ooo.api import (
    BasecampApiError,
    BasecampAuthError,
    BasecampClient,
)
from custom_components.basecamp_ooo.const import USER_AGENT

ACCOUNT_ID = 999
BASE = f"https://3.basecampapi.com/{ACCOUNT_ID}"
ENTRIES = f"{BASE}/schedules/42/entries.json"
OOO = f"{BASE}/people/9990004/out_of_office.json"


class _CompatResponse(aiohttp.ClientResponse):
    """ClientResponse accepting aioresponses 0.7.9's kwargs on aiohttp 3.14.

    aiohttp 3.14 added a required ``stream_writer`` argument that
    aioresponses 0.7.9 does not pass.
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("stream_writer", Mock(output_size=0))
        super().__init__(*args, **kwargs)


class CompatAioresponses(aioresponses):
    """aioresponses that builds _CompatResponse objects by default."""

    def add(self, *args, **kwargs) -> None:
        kwargs.setdefault("response_class", _CompatResponse)
        super().add(*args, **kwargs)


async def _token() -> str:
    return "tok-123"


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
def client(session: aiohttp.ClientSession) -> BasecampClient:
    return BasecampClient(session, _token, ACCOUNT_ID)


def _next(url: str) -> dict[str, str]:
    return {"Link": f'<{url}>; rel="next"'}


def _requests(mocked: aioresponses, method: str, url: str) -> list:
    return mocked.requests[(method, URL(url))]


async def test_requests_send_bearer_and_user_agent(client: BasecampClient) -> None:
    with CompatAioresponses() as mocked:
        mocked.get(f"{BASE}/my/profile.json", payload={"id": 9990004})

        assert await client.get_person_id() == 9990004

        (call,) = _requests(mocked, "GET", f"{BASE}/my/profile.json")
        headers = call.kwargs["headers"]
        assert headers["Authorization"] == "Bearer tok-123"
        assert headers["User-Agent"] == USER_AGENT


async def test_list_schedule_entries_follows_link_header(
    client: BasecampClient,
) -> None:
    with CompatAioresponses() as mocked:
        mocked.get(ENTRIES, payload=[{"id": 1}], headers=_next(f"{ENTRIES}?page=2"))
        mocked.get(
            f"{ENTRIES}?page=2", payload=[{"id": 2}], headers=_next(f"{ENTRIES}?page=3")
        )
        mocked.get(f"{ENTRIES}?page=3", payload=[{"id": 3}])

        entries = await client.list_schedule_entries(42)

    assert entries == [{"id": 1}, {"id": 2}, {"id": 3}]


async def test_list_schedule_entries_retries_after_429(
    client: BasecampClient,
) -> None:
    with (
        CompatAioresponses() as mocked,
        patch(
            "custom_components.basecamp_ooo.api.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        mocked.get(ENTRIES, payload=[{"id": 1}], headers=_next(f"{ENTRIES}?page=2"))
        mocked.get(f"{ENTRIES}?page=2", status=429, headers={"Retry-After": "1"})
        mocked.get(f"{ENTRIES}?page=2", payload=[{"id": 2}])

        entries = await client.list_schedule_entries(42)

    sleep.assert_awaited_once_with(1)
    assert entries == [{"id": 1}, {"id": 2}]


async def test_gives_up_after_5_consecutive_429s(client: BasecampClient) -> None:
    with (
        CompatAioresponses() as mocked,
        patch(
            "custom_components.basecamp_ooo.api.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        mocked.get(ENTRIES, status=429, headers={"Retry-After": "2"}, repeat=True)

        with pytest.raises(BasecampApiError):
            await client.list_schedule_entries(42)

        assert len(_requests(mocked, "GET", ENTRIES)) == 5
    assert sleep.await_count == 4


async def test_429_without_retry_after_waits_default(client: BasecampClient) -> None:
    with (
        CompatAioresponses() as mocked,
        patch(
            "custom_components.basecamp_ooo.api.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        mocked.get(f"{BASE}/my/profile.json", status=429)
        mocked.get(f"{BASE}/my/profile.json", payload={"id": 7})

        assert await client.get_person_id() == 7

    sleep.assert_awaited_once_with(10)


async def test_retry_after_capped_at_60(client: BasecampClient) -> None:
    with (
        CompatAioresponses() as mocked,
        patch(
            "custom_components.basecamp_ooo.api.asyncio.sleep", new_callable=AsyncMock
        ) as sleep,
    ):
        mocked.get(
            f"{BASE}/my/profile.json", status=429, headers={"Retry-After": "300"}
        )
        mocked.get(f"{BASE}/my/profile.json", payload={"id": 7})

        assert await client.get_person_id() == 7

    sleep.assert_awaited_once_with(60)


async def test_sleeps_after_releasing_rate_limited_response(
    client: BasecampClient, session: aiohttp.ClientSession
) -> None:
    """The 429 response is released before waiting, not held during the sleep."""
    open_responses = 0
    original_request = session.request

    def tracking_request(*args, **kwargs):
        ctx = original_request(*args, **kwargs)

        class _Tracking:
            async def __aenter__(self):
                nonlocal open_responses
                open_responses += 1
                return await ctx.__aenter__()

            async def __aexit__(self, *exc):
                nonlocal open_responses
                open_responses -= 1
                return await ctx.__aexit__(*exc)

        return _Tracking()

    async def check_released(_delay: float) -> None:
        assert open_responses == 0

    with (
        CompatAioresponses() as mocked,
        patch.object(session, "request", tracking_request),
        patch(
            "custom_components.basecamp_ooo.api.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=check_released,
        ) as sleep,
    ):
        mocked.get(f"{BASE}/my/profile.json", status=429, headers={"Retry-After": "1"})
        mocked.get(f"{BASE}/my/profile.json", payload={"id": 7})

        assert await client.get_person_id() == 7

    sleep.assert_awaited_once_with(1)


async def test_401_raises_auth_error(client: BasecampClient) -> None:
    with CompatAioresponses() as mocked:
        mocked.get(f"{BASE}/my/profile.json", status=401)

        with pytest.raises(BasecampAuthError):
            await client.get_person_id()


async def test_other_error_status_raises_api_error(client: BasecampClient) -> None:
    with CompatAioresponses() as mocked:
        mocked.get(f"{BASE}/my/profile.json", status=500)

        with pytest.raises(BasecampApiError):
            await client.get_person_id()


async def test_network_error_raises_api_error(client: BasecampClient) -> None:
    with CompatAioresponses() as mocked:
        mocked.get(
            f"{BASE}/my/profile.json", exception=aiohttp.ClientConnectionError("boom")
        )

        with pytest.raises(BasecampApiError):
            await client.get_person_id()


async def test_set_out_of_office_posts_expected_body(client: BasecampClient) -> None:
    with CompatAioresponses() as mocked:
        mocked.post(
            OOO,
            payload={
                "enabled": True,
                "ongoing": False,
                "start_date": "2026-11-13",
                "end_date": "2026-11-16",
            },
        )

        result = await client.set_out_of_office(
            9990004, date(2026, 11, 13), date(2026, 11, 16)
        )

        (call,) = _requests(mocked, "POST", OOO)
        assert call.kwargs["json"] == {
            "out_of_office": {"start_date": "2026-11-13", "end_date": "2026-11-16"}
        }

    assert result.enabled is True
    assert result.start == date(2026, 11, 13)
    assert result.end == date(2026, 11, 16)


async def test_get_out_of_office_parses_disabled(client: BasecampClient) -> None:
    with CompatAioresponses() as mocked:
        mocked.get(OOO, payload={"enabled": False, "ongoing": False})

        result = await client.get_out_of_office(9990004)

    assert result.enabled is False
    assert result.start is None
    assert result.end is None
