"""The store client itself: what it retries, and what it refuses to repeat.

No HTTP happens here. ``_request_once`` is the seam: every test replaces it
with a scripted answer, which is enough to pin the retry rules, the error
classification and the duplicate-note guard.
"""

from __future__ import annotations

import asyncio

import aiohttp
import pytest

from app.integrations.woocommerce import (
    WooCommerceClient,
    WooCommerceCredentials,
    WooCommerceError,
    is_permanent,
)

pytestmark = pytest.mark.asyncio

CREDENTIALS = WooCommerceCredentials("https://shop.example", "ck_x", "cs_y")


@pytest.fixture
def no_sleep(monkeypatch):
    """Run the backoff instantly, and record what it would have waited."""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return slept


class ScriptedClient(WooCommerceClient):
    """A client whose answers come from a list instead of the network."""

    def __init__(self, answers, **kw) -> None:
        super().__init__(CREDENTIALS, **kw)
        self.answers = list(answers)
        self.requests: list[tuple[str, str]] = []

    async def _request_once(self, session, method, path, **kw):
        self.requests.append((method, path))
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return answer(method, path, kw)
        return answer


def transient(message: str = "boom") -> WooCommerceError:
    return WooCommerceError(message)


def permanent(message: str = "nope") -> WooCommerceError:
    return WooCommerceError(message, permanent=True)


# ---------------------------------------------------------------------------
# Immediate retries
# ---------------------------------------------------------------------------
async def test_a_momentary_failure_is_retried_on_the_spot(no_sleep):
    client = ScriptedClient(
        [transient(), transient(), {"id": 7, "number": "1234567"}, None],
        quick_retries=2,
    )

    assert await client.update_order("1234567", status="completed") == 7
    # Three attempts at the lookup, then the status update.
    assert client.requests[:3] == [("GET", "orders/1234567")] * 3
    assert no_sleep == [2.0, 4.0]


async def test_a_permanent_failure_is_never_repeated(no_sleep):
    client = ScriptedClient([permanent("HTTP 401: invalid signature")], quick_retries=3)

    with pytest.raises(WooCommerceError) as raised:
        await client.update_order("1234567", status="completed")

    assert is_permanent(raised.value)
    assert no_sleep == []


async def test_the_store_s_own_rate_limit_sets_the_wait(no_sleep):
    client = ScriptedClient(
        [
            WooCommerceError("HTTP 429", retry_after=11.0),
            {"id": 7, "number": "1234567"},
            None,
        ],
        quick_retries=2,
    )

    await client.update_order("1234567", status="completed")

    assert no_sleep == [11.0]


async def test_running_out_of_immediate_retries_raises_the_last_error(no_sleep):
    client = ScriptedClient([transient("still down")] * 3, quick_retries=2)

    with pytest.raises(WooCommerceError) as raised:
        await client.update_order("1234567", status="completed")

    assert "still down" in str(raised.value)
    # Still transient: the retry worker will pick the order up later.
    assert is_permanent(raised.value) is False


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "final"),
    [
        (408, False),
        (429, False),
        (500, False),
        (502, False),
        (503, False),
        (504, False),
        (400, True),
        (401, True),
        (403, True),
        (404, True),
    ],
)
def test_http_statuses_are_split_into_retryable_and_final(status, final):
    """A busy or broken server is worth another try; a refusal is not."""
    from app.integrations.woocommerce import TRANSIENT_STATUSES

    assert (status not in TRANSIENT_STATUSES) is final


async def test_a_timeout_says_how_long_it_waited(monkeypatch):
    client = WooCommerceClient(CREDENTIALS, timeout=25, quick_retries=0)

    class HangingSession:
        def request(self, *a, **kw):
            raise asyncio.TimeoutError

    with pytest.raises(WooCommerceError) as raised:
        await client._request_once(HangingSession(), "GET", "orders")

    # The bare TimeoutError an admin used to see carried no message at all.
    assert "25s" in str(raised.value)
    assert "GET orders" in str(raised.value)
    assert not is_permanent(raised.value)


async def test_an_unreachable_store_is_named_as_such():
    client = WooCommerceClient(CREDENTIALS, quick_retries=0)

    class BrokenSession:
        def request(self, *a, **kw):
            raise aiohttp.ClientOSError("connection refused")

    with pytest.raises(WooCommerceError, match="cannot reach the store"):
        await client._request_once(BrokenSession(), "GET", "orders")


# ---------------------------------------------------------------------------
# Finding the order without scanning the whole shop
# ---------------------------------------------------------------------------
async def test_the_order_is_read_by_id_before_any_search(no_sleep):
    client = ScriptedClient([{"id": 2795541, "number": "2795541"}, None])

    await client.update_order("2795541", status="completed")

    assert client.requests[0] == ("GET", "orders/2795541")
    assert ("GET", "orders") not in client.requests


async def test_a_renumbered_order_still_falls_back_to_the_search(no_sleep):
    client = ScriptedClient(
        [
            # The id exists but belongs to a different human number.
            {"id": 12, "number": "999"},
            [{"id": 34, "number": "1234567"}],
            None,
        ]
    )

    await client.update_order("1234567", status="completed")

    assert client.requests[:2] == [("GET", "orders/1234567"), ("GET", "orders")]
    assert client.requests[-1] == ("PUT", "orders/34")


async def test_an_unreachable_store_does_not_turn_into_a_search(no_sleep):
    """A search would fail the same way, only after scanning every order."""
    client = ScriptedClient([transient(), transient()], quick_retries=1)

    with pytest.raises(WooCommerceError):
        await client.update_order("1234567", status="completed")

    assert client.requests == [("GET", "orders/1234567")] * 2


async def test_an_unknown_order_is_final(no_sleep):
    client = ScriptedClient([permanent("HTTP 404"), []])

    with pytest.raises(WooCommerceError) as raised:
        await client.update_order("1234567", status="completed")

    assert is_permanent(raised.value)
    assert "not found in the store" in str(raised.value)


# ---------------------------------------------------------------------------
# The note is the one call that must not be repeated blindly
# ---------------------------------------------------------------------------
async def test_a_repeat_attempt_skips_a_note_the_store_already_has(no_sleep):
    client = ScriptedClient(
        [
            {"id": 7, "number": "1234567"},
            None,  # PUT status
            [{"note": "سفارش انجام شد"}],  # GET notes
        ]
    )

    await client.update_order(
        "1234567", status="completed", note="سفارش انجام شد", repeat_attempt=True
    )

    assert ("POST", "orders/7/notes") not in client.requests


async def test_a_repeat_attempt_adds_a_note_the_store_is_missing(no_sleep):
    client = ScriptedClient(
        [
            {"id": 7, "number": "1234567"},
            None,
            [{"note": "something else"}],
            None,
        ]
    )

    await client.update_order(
        "1234567", status="completed", note="سفارش انجام شد", repeat_attempt=True
    )

    assert client.requests[-1] == ("POST", "orders/7/notes")


async def test_a_note_that_timed_out_is_not_written_twice(no_sleep):
    """The store committed the note and then the answer was lost."""
    client = ScriptedClient(
        [
            {"id": 7, "number": "1234567"},
            None,
            transient("no answer from the store within 30s"),  # POST
            [{"note": "سفارش انجام شد"}],  # the retry looks first
        ],
        quick_retries=1,
    )

    await client.update_order("1234567", status="completed", note="سفارش انجام شد")

    posts = [r for r in client.requests if r[0] == "POST"]
    assert len(posts) == 1


async def test_the_first_attempt_posts_its_note_without_asking(no_sleep):
    client = ScriptedClient([{"id": 7, "number": "1234567"}, None, None])

    await client.update_order("1234567", status="completed", note="سفارش انجام شد")

    assert client.requests == [
        ("GET", "orders/1234567"),
        ("PUT", "orders/7"),
        ("POST", "orders/7/notes"),
    ]


# ---------------------------------------------------------------------------
# Misconfiguration
# ---------------------------------------------------------------------------
async def test_missing_credentials_are_a_final_failure():
    client = WooCommerceClient(WooCommerceCredentials("", "", ""))

    with pytest.raises(WooCommerceError) as raised:
        await client.update_order("1234567", status="completed")

    assert is_permanent(raised.value)
