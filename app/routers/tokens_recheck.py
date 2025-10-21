# app/routers/tokens_recheck.py
# Дополняет раздел токенов командой /recheck_all (быстрый health-check всех аккаунтов пользователя).
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account
from app.services.token_health import recheck_user_tokens
from sqlalchemy import select
from app.database.models import async_session, Account

router = Router(name="tokens_recheck")


@router.message(Command("recheck_all"))
async def recheck_all_cmd(message: Message) -> None:
    """
    Быстрый health-check всех аккаунтов пользователя.
    Форматирует краткий отчёт по каждому аккаунту.
    """
    tg_user_id = message.from_user.id

    # Узнаём список аккаунтов пользователя (чтобы красиво вывести имена)
    async with async_session() as session:
        accs = (await session.execute(
            select(Account).where(Account.tg_user_id == tg_user_id).order_by(Account.id)
        )).scalars().all()

    if not accs:
        await message.answer("Нет аккаунтов для проверки. Добавь токен в разделе 📂 Accounts.")
        return

    summary = await recheck_user_tokens(tg_user_id)

    # Сопоставим id -> title
    titles = {a.id: (a.title or f"account {a.id}") for a in accs}

    lines = [f"✅ Проверено: {summary.get('total', 0)} — ok: {summary.get('ok', 0)}, error: {summary.get('error', 0)}"]
    details = summary.get("details", [])
    for acc_id, title, status, msg in details:
        # если в summary заголовки уже есть — используем их, иначе подставим из titles
        name = title or titles.get(acc_id, f"account {acc_id}")
        line = f"• {name} (id={acc_id}): {status or '—'}"
        if msg and status != "ok":
            line += f" — {msg}"
        lines.append(line)

    await message.answer("\n".join(lines))
