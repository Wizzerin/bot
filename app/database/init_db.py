# app/database/init_db.py
from __future__ import annotations
import logging

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
# Берём всё из моделей
from app.database.models import Base, async_engine as models_engine, DATABASE_URL, async_session

log = logging.getLogger(__name__)

def _ensure_engine() -> AsyncEngine:
    """
    Возвращаем рабочий AsyncEngine для init_db.
    1) Пытаемся использовать async_engine из models.py (models_engine).
    2) Если по какой-то причине он None/ломан, создаём свежий движок по DATABASE_URL.
    """
    eng = models_engine
    if eng is not None:
        return eng
    log.warning("init_db: models_engine is None — creating a fresh engine from DATABASE_URL")
    return create_async_engine(DATABASE_URL, echo=False, future=True)

async def init_db() -> None:
    """
    Создаёт отсутствующие таблицы по моделям (idempotent).
    Не зависит от async_session.bind.
    """
    engine = _ensure_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("DB init: metadata.create_all done")

    # Диагностика: проверим что таблица jobs теперь существует
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1 FROM jobs LIMIT 1"))
        log.info("DB init: table 'jobs' is present")
    except OperationalError:
        log.error(
            "Table 'jobs' still missing. Убедись, что класс Job объявлен и импортирован в app.database.models "
            "до вызова init_db()."
        )
        raise
    await ensure_column_if_missing("accounts", "token_status", "TEXT")
    await ensure_column_if_missing("accounts", "token_status_msg", "TEXT")
    await ensure_column_if_missing("accounts", "token_checked_at", "TIMESTAMP")



async def ensure_column_if_missing(table: str, column: str, ddl: str) -> None:
    """
    Простая 'ленивая миграция': если в таблице нет колонки — добавляем её.
    Использует SQLite PRAGMA table_info.
    Пример вызова:
        await ensure_column_if_missing("accounts", "token_status", "TEXT")
    """
    async with async_session() as session:
        # Узнаем список существующих колонок
        res = await session.execute(text(f"PRAGMA table_info({table})"))
        cols = {row[1] for row in res.fetchall()}  # row[1] — имя колонки
        if column in cols:
            return

        log.info(f"[init_db] Adding missing column: {table}.{column} ({ddl})")
        await session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        await session.commit()
