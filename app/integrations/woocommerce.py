"""Minimal WooCommerce REST client.

Only two operations are needed: change an order's status, and optionally
attach a note. Both are done against the store's own order *number*, which is
what the source message carries -- WooCommerce's internal id is not known to
us, so the order is looked up first.

Credentials are sent with HTTP Basic auth over HTTPS, which is what the
WooCommerce REST API expects for consumer key/secret pairs.

Failures are split in two, because they need opposite treatment:

* **transient** -- a timeout, a dropped connection, a 5xx, a rate limit.
  The same call a minute later usually works, so it is retried immediately a
  couple of times and then handed to the retry worker.
* **permanent** -- wrong credentials, an unknown order, a status the store
  rejects. Repeating it can only fail again, so it goes straight to an admin.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import aiohttp

from app.utils.logging import get_logger

logger = get_logger(__name__)

#: Used when no policy is supplied (the admin-panel connection test).
DEFAULT_TIMEOUT = 30
DEFAULT_QUICK_RETRIES = 2
#: Seconds before the first immediate retry; doubled for each further one.
QUICK_RETRY_BASE_DELAY = 2.0

#: Answers that mean "busy, rate limited or momentarily broken" rather than
#: "no". The 52x range is Cloudflare's, which many stores sit behind.
TRANSIENT_STATUSES = frozenset(
    {408, 425, 429, 500, 502, 503, 504, 507, 520, 521, 522, 523, 524}
)


class WooCommerceError(Exception):
    """A store call failed in a way worth reporting to an admin.

    ``permanent`` marks a failure that a later identical call cannot fix.
    """

    def __init__(
        self, message: str, *, permanent: bool = False, retry_after: float | None = None
    ) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


def is_permanent(error: BaseException) -> bool:
    """Whether retrying this failure is pointless.

    Anything unrecognised counts as transient: a retry costs one scheduled
    attempt, while wrongly giving up costs an order that never reaches the
    store.
    """
    return isinstance(error, WooCommerceError) and error.permanent


def describe_error_body(body: str) -> str:
    """The store's own explanation, in readable form.

    WooCommerce answers errors with JSON whose ``message`` is written in the
    store's language and escaped as JSON unicode escapes. Showing that raw
    makes a Persian
    message unreadable for the admin who has to act on it, so the message is
    decoded and used on its own; anything unexpected falls back to the body.
    """
    try:
        payload = json.loads(body)
    except ValueError:
        return body[:300]
    if isinstance(payload, dict):
        message = str(payload.get("message") or "").strip()
        code = str(payload.get("code") or "").strip()
        if message and code:
            return f"{message} ({code})"
        if message:
            return message
    return body[:300]


def _retry_after(response: aiohttp.ClientResponse) -> float | None:
    raw = response.headers.get("Retry-After")
    try:
        return min(60.0, max(1.0, float(raw))) if raw else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class WooCommerceCredentials:
    base_url: str
    consumer_key: str
    consumer_secret: str

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.consumer_key and self.consumer_secret)

    def describe(self) -> str:
        """Safe for display: never reveals the secret."""
        if not self.configured:
            return "not configured"
        return f"{self.base_url} (key {self.consumer_key[:6]}…)"


class WooCommerceClient:
    def __init__(
        self,
        credentials: WooCommerceCredentials,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        quick_retries: int = DEFAULT_QUICK_RETRIES,
    ) -> None:
        self.credentials = credentials
        self.timeout = timeout
        self.quick_retries = quick_retries

    def _url(self, path: str) -> str:
        return f"{self.credentials.base_url}/wp-json/wc/v3/{path.lstrip('/')}"

    def _auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(
            self.credentials.consumer_key, self.credentials.consumer_secret
        )

    def _client_timeout(self) -> aiohttp.ClientTimeout:
        # A separate, shorter connect budget: a store whose TCP handshake
        # hangs should fail fast rather than eat the whole request timeout.
        return aiohttp.ClientTimeout(total=self.timeout, connect=min(15, self.timeout))

    async def _request_once(
        self, session: aiohttp.ClientSession, method: str, path: str, **kw
    ):
        try:
            async with session.request(
                method, self._url(path), auth=self._auth(), **kw
            ) as response:
                body = await response.text()
                if response.status >= 400:
                    # WooCommerce returns a JSON body with "message" on errors.
                    raise WooCommerceError(
                        f"HTTP {response.status}: {describe_error_body(body)}",
                        permanent=response.status not in TRANSIENT_STATUSES,
                        retry_after=_retry_after(response),
                    )
                if not body:
                    return None
                try:
                    return json.loads(body)
                except ValueError as error:
                    raise WooCommerceError(
                        f"store returned non-JSON: {body[:200]}"
                    ) from error
        except asyncio.TimeoutError as error:
            # The original message is empty, which tells an admin nothing.
            raise WooCommerceError(
                f"no answer from the store within {self.timeout}s ({method} {path})"
            ) from error
        except aiohttp.ClientError as error:
            raise WooCommerceError(
                f"cannot reach the store ({type(error).__name__}: {error})"
            ) from error

    async def _request(
        self,
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        *,
        retries: int | None = None,
        **kw,
    ):
        """One call, repeated immediately while the failure looks momentary."""
        attempts = max(1, (self.quick_retries if retries is None else retries) + 1)
        for attempt in range(1, attempts + 1):
            try:
                return await self._request_once(session, method, path, **kw)
            except WooCommerceError as error:
                if error.permanent or attempt == attempts:
                    raise
                delay = error.retry_after or QUICK_RETRY_BASE_DELAY * 2 ** (attempt - 1)
                logger.warning(
                    "woocommerce_request_retry",
                    method=method,
                    path=path,
                    attempt=attempt,
                    attempts=attempts,
                    delay=delay,
                    error=str(error),
                )
                await asyncio.sleep(delay)

    async def find_order_id(
        self, session: aiohttp.ClientSession, order_number: str
    ) -> int | None:
        """Resolve the store's internal order id from its order number.

        Without a renumbering plugin the number *is* the id, and reading one
        order by id is a primary-key lookup. The search endpoint scans every
        order instead, which is what times out on a busy store -- so it is
        kept as the fallback for stores whose ``number`` really does differ.
        """
        if order_number.isdigit():
            try:
                direct = await self._request(
                    session, "GET", f"orders/{int(order_number)}"
                )
            except WooCommerceError as error:
                if not error.permanent:
                    # The store is unreachable, not merely missing this id:
                    # a search would fail the same way, only slower.
                    raise
                direct = None
            if direct and str(direct.get("number", "")).strip() == order_number:
                return int(direct["id"])

        found = await self._request(
            session, "GET", "orders", params={"search": order_number, "per_page": 20}
        )
        for candidate in found or []:
            if str(candidate.get("number", "")).strip() == order_number:
                return int(candidate["id"])
        return None

    async def _note_already_added(
        self, session: aiohttp.ClientSession, order_id: int, note: str
    ) -> bool:
        """Did an earlier attempt already write this exact note?

        Adding a note is the one call here that is not idempotent, so before
        a repeat the existing notes are read. A timeout can strike after the
        store committed the note, and the admin should not find it twice.
        """
        existing = await self._request(
            session, "GET", f"orders/{order_id}/notes", params={"per_page": 50}
        )
        wanted = note.strip()
        for entry in existing or []:
            if str(entry.get("note", "")).strip() == wanted:
                return True
        return False

    async def update_order(
        self,
        order_number: str,
        *,
        status: str | None = None,
        note: str | None = None,
        repeat_attempt: bool = False,
    ) -> int:
        """Set the status and/or add a note. Returns the store order id.

        ``repeat_attempt`` says this order was tried before, which makes the
        note call check for its own duplicate first.
        """
        if not self.credentials.configured:
            raise WooCommerceError(
                "WooCommerce credentials are not configured", permanent=True
            )

        async with aiohttp.ClientSession(timeout=self._client_timeout()) as session:
            order_id = await self.find_order_id(session, order_number)
            if order_id is None:
                raise WooCommerceError(
                    f"order {order_number} not found in the store", permanent=True
                )

            if status:
                await self._request(
                    session, "PUT", f"orders/{order_id}", json={"status": status}
                )
            if note:
                await self._add_note(
                    session, order_id, note, verify_first=repeat_attempt
                )
        logger.info(
            "woocommerce_order_updated",
            order_number=order_number,
            store_order_id=order_id,
            status=status,
            note=bool(note),
        )
        return order_id

    async def _add_note(
        self,
        session: aiohttp.ClientSession,
        order_id: int,
        note: str,
        *,
        verify_first: bool,
    ) -> None:
        payload = {"note": note, "customer_note": False}
        for attempt in range(1, self.quick_retries + 2):
            if (verify_first or attempt > 1) and await self._note_already_added(
                session, order_id, note
            ):
                return
            try:
                # Retries are driven here, one duplicate check per attempt.
                await self._request(
                    session, "POST", f"orders/{order_id}/notes", json=payload, retries=0
                )
                return
            except WooCommerceError as error:
                if error.permanent or attempt > self.quick_retries:
                    raise
                await asyncio.sleep(
                    error.retry_after or QUICK_RETRY_BASE_DELAY * 2 ** (attempt - 1)
                )

    async def ping(self) -> str:
        """Admin-panel connectivity check; never raises."""
        if not self.credentials.configured:
            return "credentials are incomplete"
        try:
            async with aiohttp.ClientSession(timeout=self._client_timeout()) as session:
                # No retries: an admin pressing "test" wants the answer now.
                await self._request(
                    session, "GET", "orders", params={"per_page": 1}, retries=0
                )
        except WooCommerceError as error:
            return str(error)
        except Exception as error:  # noqa: BLE001 - surfaced to the admin verbatim
            return f"{type(error).__name__}: {error}"
        return "ok"
