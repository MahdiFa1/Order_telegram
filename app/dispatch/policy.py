"""How hard the bot tries again, and when it gives up.

Two things can fail after an order is already finished: the update sent to
the store, and the result sent to Telegram (with the acknowledgement
reaction that follows it). Neither has any event left that would wake it, so
both carry a schedule of their own. The shape of that schedule is the same
for both -- an attempt budget and a doubling delay -- so it lives here once.

Every value is an admin-panel setting rather than a constant, because the
right answer depends on the deployment: a shop on shared hosting needs a
longer timeout and a slower schedule than one behind a CDN. The bounds below
are what the panel offers; :meth:`SettingRepository.get_int` clamps to them,
so a stale or hand-edited row can never leave the retry worker without a
usable policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.utils.enums import RetryAlertMode, SettingKey

#: (default, minimum, maximum) for each numeric option.
MAX_ATTEMPTS = (5, 1, 20)
BASE_MINUTES = (2, 1, 240)
MAX_MINUTES = (60, 1, 1440)
REQUEST_TIMEOUT = (30, 5, 120)
QUICK_RETRIES = (2, 0, 5)

#: Seconds between the immediate in-call retries: 2, 4, 8, 16, 32.
QUICK_RETRY_BASE_DELAY = 2.0


@dataclass(frozen=True, slots=True)
class RetrySchedule:
    """When the next automatic attempt happens, and whether there is one."""

    enabled: bool = True
    max_attempts: int = MAX_ATTEMPTS[0]
    base_minutes: int = BASE_MINUTES[0]
    max_minutes: int = MAX_MINUTES[0]
    alert_mode: RetryAlertMode = RetryAlertMode.EXHAUSTED

    def delay_after(self, attempt: int) -> timedelta:
        """Wait before attempt ``attempt + 1``: 2, 4, 8, 16 … minutes."""
        minutes = self.base_minutes * (2 ** max(0, attempt - 1))
        return timedelta(minutes=min(minutes, self.max_minutes))

    def has_budget_after(self, attempt: int, max_attempts: int | None = None) -> bool:
        """Is another automatic attempt allowed once ``attempt`` failed?

        ``max_attempts`` overrides the policy's own budget, which is what the
        acknowledgement passes: its per-status configuration already owns how
        many times its reaction may be retried.
        """
        budget = self.max_attempts if max_attempts is None else max_attempts
        return self.enabled and attempt < budget


@dataclass(frozen=True, slots=True)
class TelegramRetryPolicy(RetrySchedule):
    """Result dispatch and the acknowledgement reaction that follows it."""


@dataclass(frozen=True, slots=True)
class StoreRetryPolicy(RetrySchedule):
    """The WooCommerce update, which also owns its HTTP behaviour."""

    request_timeout: int = REQUEST_TIMEOUT[0]
    quick_retries: int = QUICK_RETRIES[0]

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


async def _numbers(session, keys: dict) -> dict:
    """Read and clamp a group of numeric settings in one go."""
    from app.database.repositories import SettingRepository

    settings = SettingRepository(session)
    values: dict[str, int] = {}
    for name, (key, bounds) in keys.items():
        default, low, high = bounds
        values[name] = await settings.get_int(key, default, low=low, high=high)
    return values


async def _alert_mode(session, key: str) -> RetryAlertMode:
    from app.database.repositories import SettingRepository

    try:
        return RetryAlertMode(await SettingRepository(session).get(key))
    except ValueError:
        return RetryAlertMode.EXHAUSTED


async def load_store_policy(session) -> StoreRetryPolicy:
    """Read the store's retry policy from the settings table."""
    from app.database.repositories import SettingRepository

    numbers = await _numbers(
        session,
        {
            "max_attempts": (SettingKey.WOO_RETRY_MAX_ATTEMPTS, MAX_ATTEMPTS),
            "base_minutes": (SettingKey.WOO_RETRY_BASE_MINUTES, BASE_MINUTES),
            "max_minutes": (SettingKey.WOO_RETRY_MAX_MINUTES, MAX_MINUTES),
            "request_timeout": (SettingKey.WOO_REQUEST_TIMEOUT, REQUEST_TIMEOUT),
            "quick_retries": (SettingKey.WOO_QUICK_RETRIES, QUICK_RETRIES),
        },
    )
    return StoreRetryPolicy(
        enabled=await SettingRepository(session).get_bool(
            SettingKey.WOO_RETRY_ENABLED, default=True
        ),
        alert_mode=await _alert_mode(session, SettingKey.WOO_ALERT_MODE),
        **numbers,
    )


async def load_telegram_policy(session) -> TelegramRetryPolicy:
    """Read the Telegram retry policy from the settings table."""
    from app.database.repositories import SettingRepository

    numbers = await _numbers(
        session,
        {
            "max_attempts": (SettingKey.TELEGRAM_RETRY_MAX_ATTEMPTS, MAX_ATTEMPTS),
            "base_minutes": (SettingKey.TELEGRAM_RETRY_BASE_MINUTES, BASE_MINUTES),
            "max_minutes": (SettingKey.TELEGRAM_RETRY_MAX_MINUTES, MAX_MINUTES),
        },
    )
    return TelegramRetryPolicy(
        enabled=await SettingRepository(session).get_bool(
            SettingKey.TELEGRAM_RETRY_ENABLED, default=True
        ),
        alert_mode=await _alert_mode(session, SettingKey.TELEGRAM_ALERT_MODE),
        **numbers,
    )
