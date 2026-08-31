"""Statistics and reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    OperatorRepository,
    OrderRepository,
    SourceChannelRepository,
    WorkGroupRepository,
)
from app.utils.enums import OrderStatus
from app.utils.time import business_date, range_bounds_utc


@dataclass(slots=True)
class ReportPeriod:
    label: str
    first_day: date
    last_day: date

    @classmethod
    def today(cls) -> "ReportPeriod":
        day = business_date()
        return cls("Today", day, day)

    @classmethod
    def yesterday(cls) -> "ReportPeriod":
        day = business_date() - timedelta(days=1)
        return cls("Yesterday", day, day)

    @classmethod
    def last_days(cls, days: int) -> "ReportPeriod":
        last = business_date()
        first = last - timedelta(days=days - 1)
        return cls(f"Last {days} Days", first, last)

    @classmethod
    def custom(cls, first: date, last: date) -> "ReportPeriod":
        return cls(f"{first.isoformat()} → {last.isoformat()}", first, last)


@dataclass(slots=True)
class OrderReport:
    period: ReportPeriod
    total: int
    success: int
    failed: int
    pending: int
    conflict: int
    average_completion_seconds: float | None = None

    @property
    def completed(self) -> int:
        """Only finalised orders take part in the rate formulas."""
        return self.success + self.failed

    @property
    def success_rate(self) -> float:
        return (self.success / self.completed * 100) if self.completed else 0.0

    @property
    def failure_rate(self) -> float:
        return (self.failed / self.completed * 100) if self.completed else 0.0


@dataclass(slots=True)
class OperatorReport:
    user_id: int
    display_name: str
    total: int
    success: int
    failed: int
    average_completion_seconds: float | None


@dataclass(slots=True)
class SystemStatus:
    database_ok: bool
    sources: int
    work_groups: int
    operators: int
    pending_orders: int
    conflict_orders: int
    failed_dispatches: int
    failed_acknowledgements: int


class ReportService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self.session_factory = session_factory

    async def order_report(
        self,
        period: ReportPeriod,
        *,
        source_channel_id: int | None = None,
        operator_user_id: int | None = None,
    ) -> OrderReport:
        start, end = range_bounds_utc(period.first_day, period.last_day)
        async with session_scope() as session:
            orders = OrderRepository(session)
            counts = await orders.count_by_status(
                start, end, source_channel_id=source_channel_id,
                operator_user_id=operator_user_id,
            )
            average = await orders.average_completion_seconds(
                start, end, operator_user_id=operator_user_id
            )
        return OrderReport(
            period=period,
            total=sum(counts.values()),
            success=counts.get(OrderStatus.SUCCESS.value, 0),
            failed=counts.get(OrderStatus.FAILED.value, 0),
            pending=counts.get(OrderStatus.PENDING.value, 0),
            conflict=counts.get(OrderStatus.CONFLICT.value, 0),
            average_completion_seconds=average,
        )

    async def operator_reports(self, period: ReportPeriod) -> list[OperatorReport]:
        start, end = range_bounds_utc(period.first_day, period.last_day)
        async with session_scope() as session:
            orders = OrderRepository(session)
            breakdown = await orders.operator_breakdown(start, end)
            operator_repo = OperatorRepository(session)
            reports: list[OperatorReport] = []
            for user_id, counts in breakdown:
                operator = await operator_repo.get_by_user_id(user_id)
                name = (
                    operator.display_name
                    or (f"@{operator.username}" if operator and operator.username else None)
                    if operator
                    else None
                ) or str(user_id)
                average = await orders.average_completion_seconds(
                    start, end, operator_user_id=user_id
                )
                reports.append(
                    OperatorReport(
                        user_id=user_id,
                        display_name=name,
                        total=sum(counts.values()),
                        success=counts.get(OrderStatus.SUCCESS.value, 0),
                        failed=counts.get(OrderStatus.FAILED.value, 0),
                        average_completion_seconds=average,
                    )
                )
        return reports

    async def system_status(self) -> SystemStatus:
        async with session_scope() as session:
            orders = OrderRepository(session)
            acks = AcknowledgementRepository(session)
            return SystemStatus(
                database_ok=True,
                sources=await SourceChannelRepository(session).count_enabled(),
                work_groups=await WorkGroupRepository(session).count_enabled(),
                operators=await OperatorRepository(session).count_enabled(),
                pending_orders=await orders.count_pending(),
                conflict_orders=await orders.count_conflicts(),
                failed_dispatches=await acks.count_failed_dispatches(),
                failed_acknowledgements=await acks.count_failed_acknowledgements(),
            )
