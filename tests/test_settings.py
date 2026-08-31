"""Database DSN assembly.

A password is user-supplied text and may contain anything. Pasting one into
a connection string by hand corrupts it -- '@' ends the userinfo section, so
the remainder of the password is read as the hostname and every connection
fails DNS with a message that names neither the password nor the cause.
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from app.config.settings import Settings

pytestmark = pytest.mark.asyncio

HOSTILE_PASSWORDS = [
    pytest.param("2zpI@x$3XEz-x0o81£KqJ3j1g~r'h", id="at-dollar-quote-nonascii"),
    pytest.param("pa@ss", id="at"),
    pytest.param("pa/ss?word#1", id="slash-query-fragment"),
    pytest.param("pa%ss%40word", id="percent"),
    pytest.param("p:a:s:s", id="colons"),
    pytest.param("aB3xK9mQ7pL2vN8t", id="alphanumeric"),
]


def build(password: str, **overrides) -> Settings:
    return Settings(
        BOT_TOKEN="token",
        SUPERADMIN_IDS="1",
        DATABASE_URL="",
        POSTGRES_PASSWORD=password,
        POSTGRES_USER="telegram_orders",
        POSTGRES_DB="telegram_orders",
        POSTGRES_HOST="postgres",
        POSTGRES_PORT=5432,
        **overrides,
    )


@pytest.mark.parametrize("password", HOSTILE_PASSWORDS)
async def test_dsn_survives_any_password(password: str):
    url = make_url(build(password).database_url)
    assert url.host == "postgres", "the password must never leak into the host"
    assert url.port == 5432
    assert url.database == "telegram_orders"
    assert url.username == "telegram_orders"
    assert url.password == password
    assert url.drivername == "postgresql+asyncpg"


async def test_explicit_database_url_is_respected():
    settings = Settings(
        BOT_TOKEN="token",
        SUPERADMIN_IDS="1",
        DATABASE_URL="postgresql+asyncpg://someone:secret@db.internal:6543/other",
    )
    url = make_url(settings.database_url)
    assert (url.host, url.port, url.database) == ("db.internal", 6543, "other")


async def test_a_corrupted_database_url_is_rejected_with_the_cause():
    with pytest.raises(ValueError) as excinfo:
        Settings(
            BOT_TOKEN="token",
            SUPERADMIN_IDS="1",
            DATABASE_URL="postgresql+asyncpg://u:pa@ss@postgres:5432/db",
        )
    message = str(excinfo.value)
    assert "malformed" in message
    assert "special character" in message
    assert "POSTGRES_PASSWORD" in message


async def test_compose_does_not_hand_build_the_dsn():
    """The compose file must leave DSN assembly to the application."""
    from pathlib import Path

    yaml = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parents[1]
    parsed = yaml.safe_load((root / "docker-compose.yaml").read_text())
    bot_env = parsed["services"]["bot"]["environment"]

    assert "DATABASE_URL" not in bot_env, (
        "interpolating the password into a DSN breaks on '@', '/', '?' and '#'"
    )
    for required in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_HOST"):
        assert required in bot_env, f"{required} is needed to assemble the DSN"
