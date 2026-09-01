"""Minimal WooCommerce REST client.

Only two operations are needed: change an order's status, and optionally
attach a note. Both are done against the store's own order *number*, which is
what the source message carries -- WooCommerce's internal id is not known to
us, so the order is looked up first.

Credentials are sent with HTTP Basic auth over HTTPS, which is what the
WooCommerce REST API expects for consumer key/secret pairs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import aiohttp

from app.utils.logging import get_logger

logger = get_logger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


class WooCommerceError(Exception):
    """A store call failed in a way worth reporting to an admin."""


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
    def __init__(self, credentials: WooCommerceCredentials) -> None:
        self.credentials = credentials

    def _url(self, path: str) -> str:
        return f"{self.credentials.base_url}/wp-json/wc/v3/{path.lstrip('/')}"

    def _auth(self) -> aiohttp.BasicAuth:
        return aiohttp.BasicAuth(
            self.credentials.consumer_key, self.credentials.consumer_secret
        )

    async def _request(self, session: aiohttp.ClientSession, method: str, path: str, **kw):
        async with session.request(
            method, self._url(path), auth=self._auth(), **kw
        ) as response:
            body = await response.text()
            if response.status >= 400:
                # WooCommerce returns a JSON body with "message" on errors.
                raise WooCommerceError(
                    f"HTTP {response.status}: {describe_error_body(body)}"
                )
            if not body:
                return None
            try:
                return json.loads(body)
            except ValueError as error:
                raise WooCommerceError(f"store returned non-JSON: {body[:200]}") from error

    async def find_order_id(
        self, session: aiohttp.ClientSession, order_number: str
    ) -> int | None:
        """Resolve the store's internal order id from its order number.

        Plugins that renumber orders expose the human number in ``number``,
        which may differ from the internal ``id``; the search endpoint covers
        both, and the result is confirmed against ``number`` before use.
        """
        found = await self._request(
            session, "GET", "orders", params={"search": order_number, "per_page": 20}
        )
        for candidate in found or []:
            if str(candidate.get("number", "")).strip() == order_number:
                return int(candidate["id"])

        # Fall back to treating the number as the id, which is the default
        # WooCommerce behaviour when no renumbering plugin is installed.
        try:
            direct = await self._request(session, "GET", f"orders/{int(order_number)}")
        except (WooCommerceError, ValueError):
            return None
        if direct and str(direct.get("number", "")).strip() == order_number:
            return int(direct["id"])
        return None

    async def update_order(
        self, order_number: str, *, status: str | None = None, note: str | None = None
    ) -> int:
        """Set the status and/or add a note. Returns the store order id."""
        if not self.credentials.configured:
            raise WooCommerceError("WooCommerce credentials are not configured")

        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
            order_id = await self.find_order_id(session, order_number)
            if order_id is None:
                raise WooCommerceError(f"order {order_number} not found in the store")

            if status:
                await self._request(
                    session, "PUT", f"orders/{order_id}", json={"status": status}
                )
            if note:
                await self._request(
                    session,
                    "POST",
                    f"orders/{order_id}/notes",
                    json={"note": note, "customer_note": False},
                )
        logger.info(
            "woocommerce_order_updated",
            order_number=order_number,
            store_order_id=order_id,
            status=status,
            note=bool(note),
        )
        return order_id

    async def ping(self) -> str:
        """Admin-panel connectivity check; never raises."""
        if not self.credentials.configured:
            return "credentials are incomplete"
        try:
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as session:
                await self._request(session, "GET", "orders", params={"per_page": 1})
        except WooCommerceError as error:
            return str(error)
        except Exception as error:  # noqa: BLE001 - surfaced to the admin verbatim
            return f"{type(error).__name__}: {error}"
        return "ok"
