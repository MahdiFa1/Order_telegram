"""Wires the service graph together once at startup."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.acknowledgements.service import AcknowledgementService
from app.config import Settings
from app.dispatch.service import DispatchService
from app.dispatch.store import StoreDispatchService
from app.orders.service import OrderService
from app.reports.service import ReportService
from app.services.finalizer import OrderFinalizer
from app.services.notifications import AdminNotifier
from app.services.signals import SignalService
from app.services.source_reactions import SourceReactionService
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
    store: StoreDispatchService | None = None
    source_reactions: SourceReactionService | None = None
    bot_user_id: int | None = None


def build_services(
    bot: Bot, session_factory: async_sessionmaker, settings: Settings
) -> Services:
    gateway = TelegramGateway(bot, settings)
    notifier = AdminNotifier(session_factory, gateway, settings)
    source_reactions = SourceReactionService(session_factory, gateway, settings)
    orders = OrderService(session_factory, gateway, settings, source_reactions)
    dispatch = DispatchService(session_factory, gateway, settings, notifier)
    acknowledgements = AcknowledgementService(session_factory, gateway, settings, notifier)
    store = StoreDispatchService(session_factory, settings, notifier)
    finalizer = OrderFinalizer(
        session_factory,
        dispatch,
        acknowledgements,
        settings,
        notifier,
        store=store,
        source_reactions=source_reactions,
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
        store=store,
        source_reactions=source_reactions,
    )
