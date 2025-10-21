# app/config.py
# Самодостаточная конфигурация без pydantic / pydantic-settings.
# Читает .env (если установлен python-dotenv) и os.environ.

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Settings:
    TG_BOT_TOKEN: str
    DATABASE_URL: str = "sqlite+aiosqlite:///db.sqlite3"

    APP_ID: Optional[str] = None
    APP_SECRET: Optional[str] = None

    TOKEN_HEALTH_INTERVAL_HOURS: int = 24
    TOKEN_HEALTH_NOTIFY: bool = True

    # НОВОЕ: для обратной совместимости со старым кодом
    THREADS_TOKEN: Optional[str] = None   # ← ИЗМЕНЕНИЕ

    @staticmethod
    def from_env() -> "Settings":
        tg_token = os.getenv("TG_BOT_TOKEN", "").strip()
        if not tg_token:
            raise RuntimeError("TG_BOT_TOKEN is not set. Add it to environment or .env file.")

        return Settings(
            TG_BOT_TOKEN=tg_token,
            DATABASE_URL=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///db.sqlite3"),
            APP_ID=os.getenv("APP_ID") or None,
            APP_SECRET=os.getenv("APP_SECRET") or None,
            TOKEN_HEALTH_INTERVAL_HOURS=_getenv_int("TOKEN_HEALTH_INTERVAL_HOURS", 24),
            TOKEN_HEALTH_NOTIFY=_getenv_bool("TOKEN_HEALTH_NOTIFY", True),
            THREADS_TOKEN=os.getenv("THREADS_TOKEN") or None,  # ← ИЗМЕНЕНИЕ
        )

# Пытаемся загрузить .env, если есть python-dotenv; если нет — просто игнорируем.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(override=False)
except Exception:
    pass


def _getenv_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _getenv_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- обязательные / основные ---
    TG_BOT_TOKEN: str
    DATABASE_URL: str = "sqlite+aiosqlite:///db.sqlite3"

    # --- Threads API (если используешь) ---
    APP_ID: Optional[str] = None
    APP_SECRET: Optional[str] = None

    # --- здоровье токенов ---
    TOKEN_HEALTH_INTERVAL_HOURS: int = 24  # период автопроверки
    TOKEN_HEALTH_NOTIFY: bool = True       # уведомлять при ошибке

    @staticmethod
    def from_env() -> "Settings":
        tg_token = os.getenv("TG_BOT_TOKEN", "").strip()
        if not tg_token:
            raise RuntimeError(
                "TG_BOT_TOKEN is not set. Add it to environment or .env file."
            )
        IMGBB_API_KEY: str | None = None
        return Settings(
            TG_BOT_TOKEN=tg_token,
            DATABASE_URL=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///db.sqlite3"),
            APP_ID=os.getenv("APP_ID") or None,
            APP_SECRET=os.getenv("APP_SECRET") or None,
            TOKEN_HEALTH_INTERVAL_HOURS=_getenv_int("TOKEN_HEALTH_INTERVAL_HOURS", 24),
            TOKEN_HEALTH_NOTIFY=_getenv_bool("TOKEN_HEALTH_NOTIFY", True),           
        )


settings = Settings.from_env()
