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

async def _check_and_cache_account(acc: Account, notify_on_error: bool = True) -> Tuple[str, Optional[str]]:
    """Пингует Threads `/me` для аккаунта, обновляет кеш статуса в БД.
    Возвращает (status, message) где status in {"ok", "error"}.
    """
    status = "ok"
    msg = None
    try:
        me = await get_profile(acc.access_token)  # используем get_profile из threads_client
        # если захотим — можем обновить title из username
        if not acc.title and isinstance(me, dict):
            uname = me.get("username")
            if uname:
                acc.title = uname
    except ThreadsError as e:
        status, msg = "error", str(e)[:200]
    except Exception as e:
        status, msg = "error", "Unexpected error"

    # кешируем
    try:
        async with async_session() as session:
            db_acc = await session.get(Account, acc.id)
            if db_acc:
                db_acc.token_status = status
                db_acc.token_status_msg = msg
                db_acc.token_checked_at = datetime.now(timezone.utc)
                # не трогаем access_token; title — по желанию
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

    return status, msg

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
        # если никогда не проверяли — проверим
        need = False
        if not getattr(acc, "token_checked_at", None):
            need = True
        else:
            try:
                need = (datetime.now(timezone.utc) - acc.token_checked_at.replace(tzinfo=timezone.utc)) >= min_delta
            except Exception:
                need = True

        if need:
            await _check_and_cache_account(acc, notify_on_error=True)
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

    return await _check_and_cache_account(acc, notify_on_error=False)

async def check_all_tokens_for_user(tg_user_id: int):
    """Проверяет все аккаунты пользователя, но без уведомлений в чат. Возвращает summary."""
    async with async_session() as session:
        accs = (await session.execute(
            select(Account).where(Account.tg_user_id == tg_user_id)
        )).scalars().all()

    summary = {"total": len(accs), "ok": 0, "error": 0, "details": []}
    for a in accs:
        status, msg = await _check_and_cache_account(a, notify_on_error=False)
        summary["ok" if status == "ok" else "error"] += 1
        summary["details"].append((a.id, a.title or "untitled", status, msg))
        await asyncio.sleep(0.15)
    return summary

async def recheck_user_tokens(tg_user_id: int):
    return await check_all_tokens_for_user(tg_user_id)