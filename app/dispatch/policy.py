"""How hard the bot tries to reach the store, and when it gives up.

Every value is an admin-panel setting rather than a constant, because the
right answer depends on the store: a shop on shared hosting needs a longer
timeout and a slower schedule than one behind a CDN. The bounds below are
what the panel offers; :meth:`SettingRepository.get_int` clamps to them, so
a stale or hand-edited row can never leave the retry worker without a
usable policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.utils.enums import SettingKey, StoreAlertMode

#: (default, minimum, maximum) for each numeric option.
MAX_ATTEMPTS = (5, 1, 20)
BASE_MINUTES = (2, 1, 240)
MAX_MINUTES = (60, 1, 1440)
REQUEST_TIMEOUT = (30, 5, 120)
QUICK_RETRIES = (2, 0, 5)

#: Seconds between the immediate in-call retries: 2, 4, 8, 16, 32.
QUICK_RETRY_BASE_DELAY = 2.0


@dataclass(frozen=True, slots=True)
class StoreRetryPolicy:
    """The whole retry behaviour of one store update, in one object."""

    enabled: bool = True
    max_attempts: int = MAX_ATTEMPTS[0]
    base_minutes: int = BASE_MINUTES[0]
    max_minutes: int = MAX_MINUTES[0]
    request_timeout: int = REQUEST_TIMEOUT[0]
    quick_retries: int = QUICK_RETRIES[0]
    alert_mode: StoreAlertMode = StoreAlertMode.EXHAUSTED

    def delay_after(self, attempt: int) -> timedelta:
        """Wait before attempt ``attempt + 1``: 2, 4, 8, 16 … minutes."""
        minutes = self.base_minutes * (2 ** max(0, attempt - 1))
        return timedelta(minutes=min(minutes, self.max_minutes))

    def has_budget_after(self, attempt: int) -> bool:
        """Is another automatic attempt allowed once ``attempt`` failed?"""
        return self.enabled and attempt < self.max_attempts

    @property
    def call_budget(self) -> float:
        """Seconds one whole store update may take, quick retries included.

        The first attempt runs inside the order pipeline, so it must finish
        in bounded time even when the store accepts the connection and then
        never answers.
        """
        attempts = self.quick_retries + 1
        backoff = sum(QUICK_RETRY_BASE_DELAY * 2**i for i in range(self.quick_retries))
        # Three requests at most: find the order, set the status, add a note.
        return min(600.0, 3 * (attempts * self.request_timeout) + backoff + 15.0)


async def load_store_policy(session) -> StoreRetryPolicy:
    """Read the policy from the settings table."""
    from app.database.repositories import SettingRepository

    settings = SettingRepository(session)

    async def number(key: str, bounds: tuple[int, int, int]) -> int:
        default, low, high = bounds
        return await settings.get_int(key, default, low=low, high=high)

    raw_mode = await settings.get(SettingKey.WOO_ALERT_MODE)
    try:
        alert_mode = StoreAlertMode(raw_mode)
    except ValueError:
        alert_mode = StoreAlertMode.EXHAUSTED

    return StoreRetryPolicy(
        enabled=await settings.get_bool(SettingKey.WOO_RETRY_ENABLED, default=True),
        max_attempts=await number(SettingKey.WOO_RETRY_MAX_ATTEMPTS, MAX_ATTEMPTS),
        base_minutes=await number(SettingKey.WOO_RETRY_BASE_MINUTES, BASE_MINUTES),
        max_minutes=await number(SettingKey.WOO_RETRY_MAX_MINUTES, MAX_MINUTES),
        request_timeout=await number(SettingKey.WOO_REQUEST_TIMEOUT, REQUEST_TIMEOUT),
        quick_retries=await number(SettingKey.WOO_QUICK_RETRIES, QUICK_RETRIES),
        alert_mode=alert_mode,
    )
