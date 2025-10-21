# app/services/token_health.py
# Периодическая и ручная валидация токенов Threads, кеш статуса в БД.
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional

from sqlalchemy import select

from app.database.models import async_session, Account
from app.services.threads_client import get_profile, ThreadsError
from app.services.notifications import notify_user
from app.config import settings

log = logging.getLogger(__name__)

async def check_and_cache_token_health(acc_id: int, notify_on_error: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Проверяет токен для конкретного аккаунта по ID, обновляет кеш статуса в БД.
    Возвращает (is_healthy: bool, message: str | None).
    """
    async with async_session() as session:
        acc = await session.get(Account, acc_id)
        if not acc:
            return False, "Account not found"

    status = "ok"
    msg = None
    try:
        me = await get_profile(acc.access_token)
        if not acc.title and isinstance(me, dict):
            uname = me.get("username")
            if uname:
                acc.title = uname
    except ThreadsError as e:
        status, msg = "error", str(e)[:200]
    except Exception as e:
        status, msg = "error", "Unexpected error"
        log.warning("Token check for acc %s failed with unexpected error: %s", acc.id, e)

    # кешируем
    try:
        async with async_session() as session:
            db_acc = await session.get(Account, acc.id)
            if db_acc:
                db_acc.token_status = status
                db_acc.token_status_msg = msg
                db_acc.token_checked_at = datetime.now(timezone.utc)
                if acc.title and not db_acc.title:
                    db_acc.title = acc.title
            await session.commit()
    except Exception as e:
        log.warning("token_health: cache update failed for acc %s: %s", acc.id, e)

    # уведомление пользователю (если включено)
    if notify_on_error and status == "error" and settings.TOKEN_HEALTH_NOTIFY:
        try:
            await notify_user(
                acc.tg_user_id,
                "⚠️ Token check failed\n"
                f"Account: <b>{acc.title or f'id={acc.id}'}</b>\n"
                f"Reason: <code>{msg or 'unknown'}</code>"
            )
        except Exception as e:
            log.warning("token_health: notify failed for acc %s: %s", acc.id, e)

    return status == "ok", msg

async def periodic_token_health() -> int:
    """Периодически вызывается планировщиком: проверяет токены у всех пользователей,
    у кого задан интервал в настройках и пришло время повторной проверки.
    Возвращает кол-во проверенных аккаунтов.
    """
    total = 0
    try:
        interval_hours = int(getattr(settings, "TOKEN_HEALTH_INTERVAL_HOURS", 24) or 24)
    except Exception:
        interval_hours = 24
    min_delta = timedelta(hours=interval_hours)

    async with async_session() as session:
        accs = (await session.execute(select(Account))).scalars().all()

    for acc in accs:
        need = False
        if not getattr(acc, "token_checked_at", None):
            need = True
        else:
            try:
                need = (datetime.now(timezone.utc) - acc.token_checked_at.replace(tzinfo=timezone.utc)) >= min_delta
            except Exception:
                need = True

        if need:
            # Используем новую функцию, которая принимает ID
            await check_and_cache_token_health(acc.id, notify_on_error=True)
            total += 1
            await asyncio.sleep(0.15)

    return total

async def check_token_for_user(tg_user_id: int) -> Tuple[str, Optional[str]]:
    """Проверка токена дефолтного (или первого) аккаунта пользователя — для кнопки/команды."""
    async with async_session() as session:
        acc = (await session.execute(
            select(Account).where(Account.tg_user_id == tg_user_id)
        )).scalars().first()

    if not acc:
        return "error", "no accounts"
    
    is_healthy, msg = await check_and_cache_token_health(acc.id, notify_on_error=False)
    return "ok" if is_healthy else "error", msg


async def recheck_user_tokens(tg_user_id: int):
    """Проверяет все аккаунты пользователя, но без уведомлений в чат. Возвращает summary."""
    async with async_session() as session:
        accs = (await session.execute(
            select(Account).where(Account.tg_user_id == tg_user_id)
        )).scalars().all()

    summary = {"total": len(accs), "ok": 0, "error": 0, "details": []}
    for a in accs:
        is_healthy, msg = await check_and_cache_token_health(a.id, notify_on_error=False)
        status = "ok" if is_healthy else "error"
        summary[status] += 1
        summary["details"].append((a.id, a.title or "untitled", status, msg))
        await asyncio.sleep(0.15)
    return summary
