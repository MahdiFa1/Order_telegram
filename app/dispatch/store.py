"""WooCommerce order-status updates for finalised orders.

Runs on the same outbox pattern as Telegram dispatch: one row per order,
claimed before the HTTP call, so a duplicate event or a restart never
updates the store twice.

A failure is not the end of the story. The store is a third party on the
open internet, so a timeout or a 502 says nothing about the order -- only
about this second. Such a call is therefore scheduled for another attempt
(2, 4, 8 … minutes later, up to the configured budget) and the admins hear
about it only once no attempt is left. A failure the store itself calls
final -- bad credentials, an unknown order -- skips the schedule and is
reported immediately, because repeating it cannot help.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

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
from app.dispatch.policy import StoreRetryPolicy, load_store_policy
from app.integrations.woocommerce import (
    WooCommerceClient,
    WooCommerceCredentials,
    WooCommerceError,
    is_permanent,
)
from app.utils.enums import AuditEvent, OrderStatus, RetryAlertMode
from app.utils.logging import get_logger
from app.utils.time import utcnow

logger = get_logger(__name__)

#: A row left in SENDING for longer than this belongs to a process that died
#: mid-call; the whole call is bounded by ``policy.call_budget`` (ten minutes
#: at the very most), so nothing live is ever stolen.
STALE_AFTER = timedelta(minutes=20)


@dataclass(slots=True)
class StoreOutcome:
    attempted: bool
    ok: bool
    reason: str
    attempts: int = 0
    #: When the next automatic attempt is due, if there will be one.
    retry_at: datetime | None = None
    permanent: bool = False


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

    # ------------------------------------------------------------------
    async def process(self, order_id: int, *, force: bool = False) -> StoreOutcome:
        """Attempt one store update, if the schedule allows it.

        ``force`` is the admin pressing "try again": it ignores both the
        backoff and the "permanent" verdict, because the admin may well have
        just fixed the credentials or the order in the store.
        """
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
            policy = await load_store_policy(session)
            target_status = call.target_status
            order_number = call.store_order_number
            display_number = order.display_number
            already_alerted = call.alerted

        credentials = WooCommerceCredentials(base_url, key, secret)
        if not credentials.configured:
            # Nothing to retry against: the admin has to supply the keys.
            detail = "WooCommerce credentials are not configured"
            async with session_scope() as session:
                await WooCommerceRepository(session).mark_failed(
                    order_id, detail, permanent=True, alerted=True
                )
                await AuditRepository(session).log(
                    AuditEvent.WOOCOMMERCE_GAVE_UP,
                    order_id=order_id,
                    level="ERROR",
                    message=f"WooCommerce update for order {order_number}: {detail}",
                )
            if self.notifier is not None and not already_alerted:
                await self.notifier.store_update_failed(
                    order_id, order_number, detail, final=True
                )
            return StoreOutcome(True, False, "credentials not configured", permanent=True)

        async with session_scope() as session:
            claimed = await WooCommerceRepository(session).claim(
                order_id,
                due_at=None if force else utcnow(),
                max_attempts=None if force else policy.max_attempts,
            )
            if claimed is None:
                return StoreOutcome(False, True, "already sent, in flight or not due")
            attempts = claimed.attempts

        client = self.client_factory(
            credentials,
            timeout=policy.request_timeout,
            quick_retries=policy.quick_retries,
        )
        try:
            await asyncio.wait_for(
                client.update_order(
                    order_number,
                    status=target_status,
                    note=note,
                    # A repeat must not add the same note twice.
                    repeat_attempt=attempts > 1,
                ),
                timeout=policy.call_budget,
            )
        except asyncio.TimeoutError:
            return await self._failed(
                order_id,
                order_number,
                WooCommerceError(
                    f"the store update did not finish within "
                    f"{int(policy.call_budget)}s"
                ),
                attempts=attempts,
                policy=policy,
                already_alerted=already_alerted,
            )
        except Exception as error:  # noqa: BLE001 - persisted, never fatal
            return await self._failed(
                order_id,
                order_number,
                error,
                attempts=attempts,
                policy=policy,
                already_alerted=already_alerted,
            )

        async with session_scope() as session:
            await WooCommerceRepository(session).mark_sent(order_id)
            await AuditRepository(session).log(
                AuditEvent.WOOCOMMERCE_UPDATED,
                order_id=order_id,
                message=(
                    f"WooCommerce order {order_number} set to "
                    f"{target_status or 'unchanged'}"
                ),
                data={
                    "note": bool(note),
                    "display_number": display_number,
                    "attempts": attempts,
                },
            )
        logger.info(
            "woocommerce_updated",
            order_id=order_id,
            order_number=order_number,
            target_status=target_status,
            attempts=attempts,
        )
        if already_alerted and self.notifier is not None:
            # The admins were told this order was stuck; close the loop.
            await self.notifier.store_update_recovered(
                order_id, order_number, attempts
            )
        return StoreOutcome(True, True, "updated", attempts=attempts)

    # ------------------------------------------------------------------
    async def _failed(
        self,
        order_id: int,
        order_number: str,
        error: BaseException,
        *,
        attempts: int,
        policy: StoreRetryPolicy,
        already_alerted: bool,
    ) -> StoreOutcome:
        detail = str(error) if isinstance(error, WooCommerceError) else (
            f"{type(error).__name__}: {error}"
        )
        permanent = is_permanent(error)
        retry_at = (
            utcnow() + policy.delay_after(attempts)
            if not permanent and policy.has_budget_after(attempts)
            else None
        )
        final = retry_at is None
        # An alert per attempt is only ever asked for explicitly; otherwise
        # the admins hear about the order once, when nothing is left to try.
        alert = (
            policy.alert_mode is RetryAlertMode.EVERY_ATTEMPT
            or (final and not already_alerted)
        )

        logger.warning(
            "woocommerce_update_failed",
            order_id=order_id,
            order_number=order_number,
            error=detail,
            attempts=attempts,
            permanent=permanent,
            retry_at=retry_at.isoformat() if retry_at else None,
        )
        async with session_scope() as session:
            await WooCommerceRepository(session).mark_failed(
                order_id,
                detail,
                permanent=permanent,
                next_attempt_at=retry_at,
                alerted=already_alerted or alert,
            )
            await AuditRepository(session).log(
                AuditEvent.WOOCOMMERCE_GAVE_UP if final
                else AuditEvent.WOOCOMMERCE_RETRY_SCHEDULED,
                order_id=order_id,
                level="ERROR" if final else "WARNING",
                message=(
                    f"WooCommerce update failed for order {order_number}: {detail}"
                ),
                data={
                    "attempts": attempts,
                    "max_attempts": policy.max_attempts,
                    "permanent": permanent,
                    "next_attempt_at": retry_at.isoformat() if retry_at else None,
                },
            )
        if alert and self.notifier is not None:
            await self.notifier.store_update_failed(
                order_id,
                order_number,
                detail,
                next_attempt=retry_at,
                attempts=attempts,
                max_attempts=policy.max_attempts,
                final=final,
            )
        return StoreOutcome(
            True,
            False,
            detail,
            attempts=attempts,
            retry_at=retry_at,
            permanent=permanent,
        )

    # ------------------------------------------------------------------
    async def retry_due(self) -> int:
        """Attempt every store call whose backoff has expired.

        Returns how many were attempted. Used by the background worker and
        by startup recovery, which face exactly the same question.
        """
        async with session_scope() as session:
            policy = await load_store_policy(session)
            store = WooCommerceRepository(session)
            released = await store.release_stale(utcnow() - STALE_AFTER)
            # With automatic retry switched off, a budget of one still lets
            # through the calls nobody has attempted even once -- a restart
            # between creating the row and making the call, say. Refusing
            # those would not be "no retries", it would be "no update".
            due = await store.due_calls(
                now=utcnow(),
                max_attempts=policy.max_attempts if policy.enabled else 1,
            )
            order_ids = [call.order_id for call in due]
        if released:
            logger.info("woocommerce_calls_released", count=released)

        attempted = 0
        for order_id in order_ids:
            try:
                outcome = await self.process(order_id)
            except Exception:  # noqa: BLE001 - one bad row never stops the rest
                logger.exception("woocommerce_retry_failed", order_id=order_id)
                continue
            if outcome.attempted:
                attempted += 1
        return attempted

    async def retry_now(self, order_id: int) -> StoreOutcome:
        """An admin asked for this one order to be tried again."""
        async with session_scope() as session:
            call = await WooCommerceRepository(session).reschedule(order_id)
        if call is None:
            return StoreOutcome(False, True, "no store update configured")
        return await self.process(order_id, force=True)
