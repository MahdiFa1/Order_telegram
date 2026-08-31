"""Application entry point: long polling + health server."""

from __future__ import annotations

import asyncio
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.token import TokenValidationError

from app.bot.handlers import root_router
from app.bot.middlewares import (
    ErrorGuardMiddleware,
    IdempotencyMiddleware,
    ServicesMiddleware,
)
from app.config import Settings, get_settings
from app.database.engine import dispose_engine, get_session_factory, init_engine
from app.health import HealthServer
from app.services.bootstrap import bootstrap
from app.services.container import build_services
from app.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)

#: Long polling must ask for these explicitly -- ``message_reaction`` in
#: particular is NOT delivered by default, and reaction-based detection would
#: silently never fire without it.
ALLOWED_UPDATES = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "callback_query",
    "message_reaction",
    "message_reaction_count",
    "my_chat_member",
]


def build_dispatcher(services) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.outer_middleware(IdempotencyMiddleware())
    dispatcher.update.outer_middleware(ServicesMiddleware(services))
    dispatcher.update.middleware(ErrorGuardMiddleware())
    dispatcher.include_router(root_router)
    return dispatcher


async def run(settings: Settings) -> None:
    # The compose file cannot enforce these (see its header comment), so the
    # checks live here, where a misconfiguration is reported clearly instead of
    # failing somewhere deep in the Telegram client.
    if not settings.bot_token:
        raise SystemExit(
            "BOT_TOKEN is not set. Set it in your deployment's environment "
            "variables (or copy .env.example to .env locally) and restart."
        )
    if not settings.superadmin_ids:
        logger.warning(
            "no_super_admins_configured",
            message=(
                "SUPERADMIN_IDS is empty: nobody will be able to open the admin "
                "panel. Set it to your numeric Telegram user id and restart."
            ),
        )

    init_engine(settings)
    session_factory = get_session_factory()

    try:
        bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
    except TokenValidationError as error:
        # Wrong shape entirely (a stray quote, a truncated paste, a username).
        raise SystemExit(
            "BOT_TOKEN is malformed. It must look like "
            "'123456789:AA...' exactly as @BotFather sent it, with no quotes "
            "or spaces. Fix it in your deployment's environment variables and "
            "redeploy."
        ) from error
    services = build_services(bot, session_factory, settings)

    health = HealthServer(settings.health_host, settings.health_port)
    await health.start()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - non-POSIX
            pass

    dispatcher = build_dispatcher(services)
    polling_task: asyncio.Task | None = None

    try:
        await bootstrap(session_factory, settings)

        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError as error:
            # Right shape, but Telegram rejected it: wrong token, or it was
            # revoked with /revoke in @BotFather.
            raise SystemExit(
                "BOT_TOKEN was rejected by Telegram (401 Unauthorized). The "
                "token is either wrong or has been revoked. Get a fresh one "
                "from @BotFather, update your deployment's environment "
                "variables and redeploy."
            ) from error
        services.bot_user_id = me.id
        health.bot_ready = True
        logger.info("bot_started", username=me.username, bot_id=me.id)

        # Finish anything a previous process left half-done before accepting
        # new updates.
        await services.finalizer.recover()
        await services.orders.process_pending_deliveries()

        polling_task = asyncio.create_task(
            dispatcher.start_polling(
                bot,
                allowed_updates=ALLOWED_UPDATES,
                handle_signals=False,
                close_bot_session=False,
            )
        )
        stop_wait = asyncio.create_task(stop_event.wait())
        await asyncio.wait(
            {polling_task, stop_wait}, return_when=asyncio.FIRST_COMPLETED
        )
        stop_wait.cancel()
    finally:
        logger.info("shutdown_started")
        health.bot_ready = False
        if polling_task is not None and not polling_task.done():
            await dispatcher.stop_polling()
            try:
                await asyncio.wait_for(polling_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                polling_task.cancel()
        try:
            # Route any album still inside its debounce window.
            await services.orders.flush_pending_albums()
        except Exception:  # noqa: BLE001
            logger.exception("album_flush_on_shutdown_failed")
        await health.stop()
        await bot.session.close()
        await dispose_engine()
        logger.info("shutdown_completed")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)
    try:
        asyncio.run(run(settings))
    except (KeyboardInterrupt, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code:
            logger.error("startup_failed", error=str(error))
            sys.exit(error.code)
        logger.info("interrupted")


if __name__ == "__main__":
    main()
