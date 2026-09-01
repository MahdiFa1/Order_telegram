"""One-time-per-start database seeding.

Creates the rows the admin panel edits (rule sets, acknowledgement configs,
default settings) and promotes every ``SUPERADMIN_IDS`` entry. Nothing here
destroys existing data, so a redeploy is always safe.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database.engine import session_scope
from app.database.repositories import (
    AcknowledgementRepository,
    AdminRepository,
    ResultConfigRepository,
    RuleRepository,
    SettingRepository,
    SourceReactionRepository,
)
from app.database.repositories.settings import DEFAULTS
from app.utils.enums import RESULT_STATUSES, SourceReactionStage
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def bootstrap(session_factory: async_sessionmaker, settings: Settings) -> None:
    async with session_scope() as session:
        admins = AdminRepository(session)
        for user_id in settings.superadmin_ids:
            await admins.upsert_super_admin(user_id)

        setting_repo = SettingRepository(session)
        existing = await setting_repo.all()
        for key, value in DEFAULTS.items():
            if key not in existing or existing.get(key) is None:
                await setting_repo.set(key, value)

        rules = RuleRepository(session)
        acks = AcknowledgementRepository(session)
        results = ResultConfigRepository(session)
        for status in RESULT_STATUSES:
            await rules.get_rule(status)
            await acks.get_config(status)
            await results.get(status)

        source_reactions = SourceReactionRepository(session)
        for stage in SourceReactionStage:
            await source_reactions.get_config(stage)

    logger.info(
        "bootstrap_completed", super_admins=len(settings.superadmin_ids)
    )
