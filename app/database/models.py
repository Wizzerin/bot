# app/database/models.py
# ------------------------------------------------------------
# Единая точка определения:
#  • Base (DeclarativeBase),
#  • async_engine + async_session,
#  • модели: Account, Job, BotSettings.
#
# DSN берём по приоритету:
#   1) app.config.settings.<DATABASE_URL | DB_URL | SQLALCHEMY_DATABASE_URL | DB_DSN>
#   2) переменные окружения (та же четвёрка имён)
#   3) fallback: sqlite+aiosqlite:///db.sqlite3
# ------------------------------------------------------------

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, Integer, Text, ForeignKey, DateTime, Column

from sqlalchemy.orm import relationship
from datetime import datetime

# ---------- DSN discovery ----------

def _discover_db_url() -> str:
    # 1) settings.* (если модуль доступен)
    try:
        from app.config import settings  # может не существовать / падать при импорте
    except Exception:
        settings = None  # type: ignore

    candidates: list[str] = []
    if settings is not None:
        for name in ("DATABASE_URL", "DB_URL", "SQLALCHEMY_DATABASE_URL", "DB_DSN"):
            val = getattr(settings, name, None)
            if val:
                candidates.append(val)

    # 2) os.environ
    if not candidates:
        for name in ("DATABASE_URL", "DB_URL", "SQLALCHEMY_DATABASE_URL", "DB_DSN"):
            val = os.getenv(name)
            if val:
                candidates.append(val)

    # 3) fallback на локальную SQLite
    if not candidates:
        candidates.append("sqlite+aiosqlite:///db.sqlite3")

    return candidates[0]


DATABASE_URL = _discover_db_url()


# ---------- Base / engine / session ----------

class Base(AsyncAttrs, DeclarativeBase):
    pass


async_engine = create_async_engine(
    DATABASE_URL,
    echo=False,   # поставь True при отладке SQL
    future=True,
)

async_session = async_sessionmaker(
    bind=async_engine,
    expire_on_commit=False,
)


# ---------- Модели ----------

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    access_token: Mapped[str] = mapped_column(String, nullable=False)

    # ---------- НОВОЕ: кеш статуса токена ----------
    token_status = Column(String(16), nullable=True)        # 'ok' / 'error' / None
    token_status_msg = Column(Text, nullable=True)          # краткая причина ошибки (если была)
    token_checked_at = Column(DateTime, nullable=True)      # когда проверяли (UTC)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    time_str: Mapped[str] = mapped_column(String, nullable=False)  # формат "HH:MM"
    text: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    dow_mask: Mapped[int] = mapped_column(Integer, nullable=False, default=127)
    media = relationship("JobMedia", backref="job", cascade="all, delete-orphan")

class JobMedia(Base):
    __tablename__ = "job_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="telegram")  # "telegram" | "url"
    tg_file_id: Mapped[str] = mapped_column(String, nullable=True)      # если source="telegram"
    url: Mapped[str] = mapped_column(String, nullable=True)             # если source="url"
    mime: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

class BotSettings(Base):
    __tablename__ = "bot_settings"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    notify_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    tz: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    default_account_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
