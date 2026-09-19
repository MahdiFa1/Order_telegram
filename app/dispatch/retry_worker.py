"""The background loop that finishes store updates nobody is watching.

Everything else in the pipeline is driven by a Telegram update: an order
arrives, an operator reacts, an admin presses a button. A store call that
failed has no such trigger -- the order is already finished, and no further
event will ever touch it. Without this loop a momentary timeout would keep
the store out of date until a human noticed the alert.

The loop owns no state: it asks the store service which calls are due and
lets that service do the work, so a retry behaves exactly like the first
attempt, including its claim and its audit trail.
"""

from __future__ import annotations

import asyncio

from app.utils.logging import get_logger

logger = get_logger(__name__)

#: How often the queue is inspected. The delay before a retry comes from the
#: schedule stored on the row, not from this interval, so a short tick only
#: means a due call is picked up promptly.
TICK_SECONDS = 30.0


class StoreRetryWorker:
    """Runs :meth:`StoreDispatchService.retry_due` until it is cancelled."""

    def __init__(self, store, interval: float = TICK_SECONDS) -> None:
        self.store = store
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        logger.info("store_retry_worker_started", interval=self.interval)
        while True:
            try:
                await asyncio.sleep(self.interval)
                attempted = await self.store.retry_due()
                if attempted:
                    logger.info("store_retry_tick", attempted=attempted)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad tick must not end the loop
                logger.exception("store_retry_tick_failed")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="store-retry-worker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutting down
            pass
        logger.info("store_retry_worker_stopped")
