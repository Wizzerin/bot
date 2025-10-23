# app/database/models.py
# ------------------------------------------------------------
# (ИЗМЕНЕНИЕ) Добавлены модели Draft и DraftMedia.
# ------------------------------------------------------------

from __future__ import annotations

import os
from typing import Optional, List # Добавлено List

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import BigInteger, String, Integer, Text, ForeignKey, DateTime, Column, Boolean

from sqlalchemy.orm import relationship
from datetime import datetime, timezone # Добавлено timezone

# ---------- DSN discovery ----------
# ... (код без изменений) ...
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
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    token_status = Column(String(16), nullable=True)
    token_status_msg = Column(Text, nullable=True)
    token_checked_at = Column(DateTime, nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    time_str: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    dow_mask: Mapped[int] = mapped_column(Integer, nullable=False, default=127)
    media: Mapped[List["JobMedia"]] = relationship("JobMedia", back_populates="job", cascade="all, delete-orphan") # Уточнено

class JobMedia(Base):
    __tablename__ = "job_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="telegram")
    tg_file_id: Mapped[Optional[str]] = mapped_column(String, nullable=True) # Сделано Optional
    url: Mapped[Optional[str]] = mapped_column(String, nullable=True) # Сделано Optional
    mime: Mapped[Optional[str]] = mapped_column(String(64), nullable=True) # Сделано Optional
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False) # Улучшен default
    job: Mapped["Job"] = relationship("Job", back_populates="media") # Добавлено для двусторонней связи


class PublishedPost(Base):
    __tablename__ = "published_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    account_id: Mapped[int] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=False)
    threads_post_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)) # Улучшен default
    has_media: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


# --- (НОВЫЕ МОДЕЛИ) ---
class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    suggested_hashtags: Mapped[Optional[str]] = mapped_column(Text, nullable=True) # Для хранения предложений ИИ
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    # Связь с медиа
    media: Mapped[List["DraftMedia"]] = relationship("DraftMedia", back_populates="draft", cascade="all, delete-orphan")

class DraftMedia(Base):
    __tablename__ = "draft_media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(Integer, ForeignKey("drafts.id", ondelete="CASCADE"), index=True)
    tg_file_id: Mapped[str] = mapped_column(String, nullable=False) # Храним только file_id из телеграма
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Связь обратно к черновику
    draft: Mapped["Draft"] = relationship("Draft", back_populates="media")
# --- Конец новых моделей ---


class BotSettings(Base):
    __tablename__ = "bot_settings"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    notify_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    tz: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    default_account_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("accounts.id"), nullable=True) # Добавлен ForeignKey

