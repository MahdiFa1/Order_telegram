"""Alembic must survive a percent-encoded password.

Any password containing '@', '$' or a non-ASCII character is percent-encoded
when the DSN is rendered. Writing that string into the Alembic config passes
it through configparser, whose interpolation treats '%' as an escape and
aborts with "invalid interpolation syntax" -- so the migration never runs and
the deployment never starts.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings

pytestmark = pytest.mark.asyncio

ROOT = Path(__file__).resolve().parents[1]

#: The characters that actually broke production, kept together on purpose.
HOSTILE_PASSWORD = "tPz\\sU$@2zpI@x$3XEz-x0o81£KqJ3j1g~r'h"


async def test_env_py_never_writes_the_dsn_into_the_alembic_config():
    """A static guard: set_main_option routes through configparser."""
    source = (ROOT / "alembic" / "env.py").read_text()
    for line in source.splitlines():
        code = line.split("#", 1)[0]
        assert "set_main_option" not in code, (
            "the DSN must reach the engine directly, not through configparser"
        )
    assert "create_async_engine(settings.database_url" in source


async def test_percent_encoded_dsn_would_break_configparser():
    """Documents precisely why the guard above exists."""
    from configparser import ConfigParser

    from sqlalchemy.engine import URL

    dsn = URL.create(
        "postgresql+asyncpg",
        username="u",
        password=HOSTILE_PASSWORD,
        host="postgres",
        port=5432,
        database="db",
    ).render_as_string(hide_password=False)
    assert "%" in dsn, "this password must percent-encode, or the test proves nothing"

    parser = ConfigParser()
    parser.add_section("alembic")
    with pytest.raises(ValueError, match="interpolation"):
        parser.set("alembic", "sqlalchemy.url", dsn)


@pytest.mark.integration
async def test_migrations_run_against_a_hostile_password(db_engine):
    """End to end: create a role with that password, migrate, count tables."""
    settings = get_settings()
    admin_url = settings.database_url

    async def sql(statement: str) -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(sa.text(statement))
        finally:
            await engine.dispose()

    try:
        await sql("DROP DATABASE IF EXISTS alembic_pw_check")
        await sql("DROP ROLE IF EXISTS alembic_pw_user")
        await sql(f"CREATE ROLE alembic_pw_user LOGIN PASSWORD $tag${HOSTILE_PASSWORD}$tag$")
        await sql("CREATE DATABASE alembic_pw_check OWNER alembic_pw_user")
    except Exception as error:  # noqa: BLE001 - needs a superuser connection
        pytest.skip(f"cannot provision a test role: {error}")

    from sqlalchemy.engine import make_url

    target = make_url(admin_url)
    env = {
        "BOT_TOKEN": "token",
        "SUPERADMIN_IDS": "1",
        "DATABASE_URL": "",
        "POSTGRES_USER": "alembic_pw_user",
        "POSTGRES_PASSWORD": HOSTILE_PASSWORD,
        "POSTGRES_DB": "alembic_pw_check",
        "POSTGRES_HOST": target.host or "127.0.0.1",
        "POSTGRES_PORT": str(target.port or 5432),
    }
    completed = await asyncio.to_thread(
        subprocess.run,
        # The console script may not be on PATH in every environment; calling
        # alembic's entry point directly is equivalent.
        [sys.executable, "-c", "from alembic.config import main; main()",
         "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"alembic failed:\n{completed.stdout}\n{completed.stderr}"
    )
    assert "Running upgrade" in completed.stderr

    from app.config.settings import Settings

    migrated = Settings(**env).database_url
    engine = create_async_engine(migrated)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(
                sa.text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            )
    finally:
        await engine.dispose()
    from app.database.models import Base

    # Every model, plus Alembic's own bookkeeping table.
    assert count == len(Base.metadata.tables) + 1
