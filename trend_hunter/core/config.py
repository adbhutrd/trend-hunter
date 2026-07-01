"""Pydantic v2 settings — env vars + .env file, prefixed TH_, case-insensitive."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TH_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── storage ──
    db_path: Path = Field(default=Path("./data/trends.duckdb"))

    # ── logging ──
    log_level: str = "INFO"
    log_dir: Path = Field(default=Path("./data/logs"))

    # ── scheduling ──
    scan_interval_minutes: int = 120
    aggregate_hour: int = 3

    # ── dashboard ──
    dash_port: int = 8501

    # ── alerts (all optional) ──
    discord_webhook: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # ── outbound mail (optional) ──
    gmail_address: str | None = None
    gmail_app_password: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor — safe to call from anywhere, any number of times."""
    return Settings()  # type: ignore[call-arg]
