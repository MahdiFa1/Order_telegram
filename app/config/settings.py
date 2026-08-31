"""Application settings loaded from environment variables.

Only credentials and the bootstrap super-admin list are read from the
environment. Everything functional (sources, groups, routes, operators,
rules, destinations, reactions) lives in PostgreSQL and is managed from the
Telegram admin panel.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Telegram ---
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    superadmin_ids: Annotated[tuple[int, ...], NoDecode] = Field(
        default=(), alias="SUPERADMIN_IDS"
    )

    # --- Database ---
    database_url: str = Field(default="", alias="DATABASE_URL")
    postgres_db: str = Field(default="telegram_orders", alias="POSTGRES_DB")
    postgres_user: str = Field(default="telegram_orders", alias="POSTGRES_USER")
    postgres_password: str = Field(default="", alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="postgres", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")

    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=10, alias="DB_MAX_OVERFLOW")
    db_echo: bool = Field(default=False, alias="DB_ECHO")

    # --- Application ---
    app_env: str = Field(default="production", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(default="json", alias="LOG_FORMAT")
    timezone: str = Field(default="Asia/Tehran", alias="TZ")

    health_host: str = Field(default="0.0.0.0", alias="HEALTH_HOST")
    health_port: int = Field(default=8080, alias="HEALTH_PORT")

    run_migrations_on_start: bool = Field(default=True, alias="RUN_MIGRATIONS_ON_START")

    telegram_max_retries: int = Field(default=3, alias="TELEGRAM_MAX_RETRIES")
    telegram_retry_base_delay: float = Field(default=1.0, alias="TELEGRAM_RETRY_BASE_DELAY")

    admin_notification_cooldown: int = Field(default=300, alias="ADMIN_NOTIFICATION_COOLDOWN")

    @field_validator("superadmin_ids", mode="before")
    @classmethod
    def _parse_superadmins(cls, value: Any) -> Any:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return tuple(
                int(chunk.strip())
                for chunk in value.replace(";", ",").split(",")
                if chunk.strip()
            )
        if isinstance(value, (list, tuple)):
            return tuple(int(v) for v in value)
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        # Raises ZoneInfoNotFoundError early instead of at first use.
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def _assemble_database_url(self) -> "Settings":
        """Build the DSN from its parts, escaping each one.

        A password may legitimately contain '@', '/', '?' or '#'. Pasting one
        into a connection string by hand silently corrupts it -- '@' in
        particular ends the userinfo section, so the rest of the password is
        read as the hostname and every connection fails DNS. ``URL.create``
        keeps the components separate and quotes them on render.
        """
        if not self.database_url:
            object.__setattr__(
                self,
                "database_url",
                URL.create(
                    "postgresql+asyncpg",
                    username=self.postgres_user,
                    password=self.postgres_password,
                    host=self.postgres_host,
                    port=self.postgres_port,
                    database=self.postgres_db,
                ).render_as_string(hide_password=False),
            )
        return self

    @model_validator(mode="after")
    def _reject_a_corrupted_dsn(self) -> "Settings":
        """Catch a hand-built DATABASE_URL whose password broke the host."""
        if not self.database_url:
            return self
        try:
            host = make_url(self.database_url).host or ""
        except Exception:  # noqa: BLE001 - reported below with context
            raise ValueError(
                "DATABASE_URL could not be parsed. If your database password "
                "contains special characters, unset DATABASE_URL and let the "
                "POSTGRES_* variables build it instead."
            ) from None
        # A hostname can only hold letters, digits, hyphens and dots. Anything
        # else means the password leaked into the host.
        illegal = {c for c in host if not (c.isalnum() or c in "-._")}
        if illegal:
            raise ValueError(
                f"DATABASE_URL is malformed: the host reads {host!r}, which "
                f"contains {''.join(sorted(illegal))!r}. This happens when the "
                "database password contains a special character such as '@'. "
                "Unset DATABASE_URL and set POSTGRES_PASSWORD instead -- the "
                "application will assemble and escape the DSN itself."
            )
        return self

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def sync_database_url(self) -> str:
        """DSN for Alembic / tooling that uses a synchronous driver."""
        return self.database_url.replace("+asyncpg", "").replace(
            "postgresql://", "postgresql+psycopg2://", 1
        ) if "+asyncpg" in self.database_url else self.database_url

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
