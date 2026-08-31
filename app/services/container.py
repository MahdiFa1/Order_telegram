"""Wires the service graph together once at startup."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.acknowledgements.service import AcknowledgementService
from app.config import Settings
from app.dispatch.service import DispatchService
from app.orders.service import OrderService
from app.reports.service import ReportService
from app.services.finalizer import OrderFinalizer
from app.services.notifications import AdminNotifier
from app.services.signals import SignalService
from app.telegram.gateway import TelegramGateway


@dataclass(slots=True)
class Services:
    settings: Settings
    session_factory: async_sessionmaker
    gateway: TelegramGateway
    notifier: AdminNotifier
    orders: OrderService
    dispatch: DispatchService
    acknowledgements: AcknowledgementService
    finalizer: OrderFinalizer
    signals: SignalService
    reports: ReportService
    bot_user_id: int | None = None


def build_services(
    bot: Bot, session_factory: async_sessionmaker, settings: Settings
) -> Services:
    gateway = TelegramGateway(bot, settings)
    notifier = AdminNotifier(session_factory, gateway, settings)
    orders = OrderService(session_factory, gateway, settings)
    dispatch = DispatchService(session_factory, gateway, settings, notifier)
    acknowledgements = AcknowledgementService(session_factory, gateway, settings, notifier)
    finalizer = OrderFinalizer(
        session_factory, dispatch, acknowledgements, settings, notifier
    )
    signals = SignalService(session_factory, finalizer)
    reports = ReportService(session_factory)
    return Services(
        settings=settings,
        session_factory=session_factory,
        gateway=gateway,
        notifier=notifier,
        orders=orders,
        dispatch=dispatch,
        acknowledgements=acknowledgements,
        finalizer=finalizer,
        signals=signals,
        reports=reports,
    )
