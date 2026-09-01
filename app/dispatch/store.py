"""WooCommerce order-status updates for finalised orders.

Runs on the same outbox pattern as Telegram dispatch: one row per order,
claimed before the HTTP call, so a duplicate event or a restart never
updates the store twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import (
    AuditRepository,
    OrderRepository,
    ResultConfigRepository,
    SettingRepository,
    WooCommerceRepository,
)
from app.integrations.woocommerce import (
    WooCommerceClient,
    WooCommerceCredentials,
    WooCommerceError,
)
from app.utils.enums import AuditEvent, OrderStatus
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class StoreOutcome:
    attempted: bool
    ok: bool
    reason: str


def render_note(template: str | None, order, status: OrderStatus) -> str | None:
    """Fill the admin-configured note template."""
    if not template:
        return None
    try:
        return template.format(
            order=order.display_number,
            number=order.source_order_number or "",
            status=status.value,
        )
    except (KeyError, IndexError, ValueError):
        return template


class StoreDispatchService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
        notifier=None,
        client_factory=None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.notifier = notifier
        #: Injected by the test suite; production builds a real client.
        self.client_factory = client_factory or WooCommerceClient

    async def prepare(self, order_id: int, status: OrderStatus) -> bool:
        """Create the outbox row when the store update is configured."""
        async with session_scope() as session:
            order = await OrderRepository(session).get(order_id)
            if order is None:
                return False
            config = await ResultConfigRepository(session).get(status)
            if not config.woo_enabled:
                return False
            if not order.source_order_number:
                await AuditRepository(session).log(
                    AuditEvent.WOOCOMMERCE_FAILED,
                    order_id=order_id,
                    level="WARNING",
                    message=(
                        "WooCommerce update skipped: this order carries no store "
                        "order number. Enable the order-number requirement so "
                        "every order has one."
                    ),
                )
                return False
            await WooCommerceRepository(session).ensure_call(
                order_id=order_id,
                order_status=status,
                store_order_number=order.source_order_number,
                target_status=config.woo_status,
            )
            return True

    async def process(self, order_id: int) -> StoreOutcome:
        async with session_scope() as session:
            store = WooCommerceRepository(session)
            call = await store.get_call(order_id)
            if call is None:
                return StoreOutcome(False, True, "no store update configured")

            order = await OrderRepository(session).get(order_id)
            if order is None:
                return StoreOutcome(False, False, "order not found")
            status = OrderStatus(call.order_status)
            config = await ResultConfigRepository(session).get(status)
            note = (
                render_note(config.woo_note, order, status)
                if config.woo_note_enabled
                else None
            )
            base_url, key, secret = await SettingRepository(session).woo_credentials()
            target_status = call.target_status
            order_number = call.store_order_number
            display_number = order.display_number

        credentials = WooCommerceCredentials(base_url, key, secret)
        if not credentials.configured:
            async with session_scope() as session:
                await WooCommerceRepository(session).mark_failed(
                    order_id, "WooCommerce credentials are not configured"
                )
            return StoreOutcome(True, False, "credentials not configured")

        async with session_scope() as session:
            if await WooCommerceRepository(session).claim(order_id) is None:
                return StoreOutcome(False, True, "already sent or in flight")

        client = self.client_factory(credentials)
        try:
            await client.update_order(order_number, status=target_status, note=note)
        except (WooCommerceError, Exception) as error:  # noqa: BLE001
            detail = f"{type(error).__name__}: {error}"
            logger.warning(
                "woocommerce_update_failed",
                order_id=order_id,
                order_number=order_number,
                error=detail,
            )
            async with session_scope() as session:
                await WooCommerceRepository(session).mark_failed(order_id, detail)
                await AuditRepository(session).log(
                    AuditEvent.WOOCOMMERCE_FAILED,
                    order_id=order_id,
                    level="ERROR",
                    message=f"WooCommerce update failed for order {order_number}: {detail}",
                )
            if self.notifier is not None:
                await self.notifier.store_update_failed(order_id, order_number, detail)
            return StoreOutcome(True, False, detail)

        async with session_scope() as session:
            await WooCommerceRepository(session).mark_sent(order_id)
            await AuditRepository(session).log(
                AuditEvent.WOOCOMMERCE_UPDATED,
                order_id=order_id,
                message=(
                    f"WooCommerce order {order_number} set to "
                    f"{target_status or 'unchanged'}"
                ),
                data={"note": bool(note), "display_number": display_number},
            )
        logger.info(
            "woocommerce_updated",
            order_id=order_id,
            order_number=order_number,
            target_status=target_status,
        )
        return StoreOutcome(True, True, "updated")
