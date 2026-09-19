"""The background loop that finishes the work nobody is watching.

Everything else in the pipeline is driven by a Telegram update: an order
arrives, an operator reacts, an admin presses a button. A result that never
reached its destination, a reaction that was refused and a store update that
timed out have no such trigger -- the order is already finished, and no
further event will ever touch it. Without this loop a momentary failure
would sit there until a human noticed the alert.

The loop owns no state: it asks the finalizer what is due and lets the
ordinary services do the work, so a retry behaves exactly like the first
attempt, including its claim and its audit trail.
"""

from __future__ import annotations

import asyncio

from app.utils.logging import get_logger

logger = get_logger(__name__)

#: How often the queues are inspected. The delay before a retry comes from
#: the schedule stored on the row, not from this interval, so a short tick
#: only means a due attempt is picked up promptly.
TICK_SECONDS = 30.0


class RetryWorker:
    """Runs :meth:`OrderFinalizer.retry_due` until it is cancelled."""

    def __init__(self, finalizer, interval: float = TICK_SECONDS) -> None:
        self.finalizer = finalizer
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        logger.info("retry_worker_started", interval=self.interval)
        while True:
            try:
                await asyncio.sleep(self.interval)
                await self.finalizer.retry_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad tick must not end the loop
                logger.exception("retry_tick_failed")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="retry-worker")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutting down
            pass
        logger.info("retry_worker_stopped")
