# app/routers/notify.py
# ------------------------------------------------------------
# Меню уведомлений для планировщика.
#  • notify_menu       — вход в подменю
#  • notify_here       — привязать текущий чат для отчётов
#  • notify_status     — показать состояние + кол-во таймеров
#  • notify_test       — отправить тестовое уведомление
#  • notify_off        — отключить отчёты
# ------------------------------------------------------------

from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.keyboards import notify_menu
from app.database.models import async_session, BotSettings, Job
from app.services.notifications import notify_user  # наша отправка через bind_bot
from app.services.safe_edit import safe_edit        # безопасное редактирование (или можешь скопировать локально)

router = Router()
log = logging.getLogger(__name__)

@router.callback_query(F.data == "notify_menu")
async def notify_menu_open(callback: CallbackQuery) -> None:
    log.info("notify_menu: open by user %s", callback.from_user.id)
    await safe_edit(callback.message, "Scheduler notifications:", reply_markup=notify_menu())
    await callback.answer()

@router.callback_query(F.data == "notify_here")
async def notify_here_cb(callback: CallbackQuery) -> None:
    """Привязываем чат текущего сообщения для отчётов планировщика."""
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    log.info("notify_here: user %s -> chat %s", user_id, chat_id)

    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        if st is None:
            st = BotSettings(tg_user_id=user_id, notify_chat_id=chat_id)
            session.add(st)
        else:
            st.notify_chat_id = chat_id
        await session.commit()

    await safe_edit(callback.message, f"✅ I will send reports here: <code>{chat_id}</code>", reply_markup=notify_menu())
    await callback.answer()

@router.callback_query(F.data == "notify_status")
async def notify_status_cb(callback: CallbackQuery) -> None:
    """Показать статус уведомлений и кол-во задач пользователя."""
    user_id = callback.from_user.id
    log.info("notify_status: user %s", user_id)

    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        jobs = (await session.execute(
            select(Job).where(Job.tg_user_id == user_id)
        )).scalars().all()

    if st and st.notify_chat_id:
        text = f"🔔 Enabled\nChat: <code>{st.notify_chat_id}</code>\nYour active timers: <b>{len(jobs)}</b>"
    else:
        text = f"🔕 Disabled\nYour active timers: <b>{len(jobs)}</b>\nEnable via “📍 Send reports here”"

    await safe_edit(callback.message, text, reply_markup=notify_menu())
    await callback.answer()

@router.callback_query(F.data == "notify_test")
async def notify_test_cb(callback: CallbackQuery) -> None:
    """Отправка тестового уведомления в привязанный чат (если настроен)."""
    user_id = callback.from_user.id
    log.info("notify_test: user %s", user_id)
    await notify_user(user_id, "🧪 Test notification from scheduler (button)")
    await callback.answer("Sent test message if chat is configured.")

@router.callback_query(F.data == "notify_off")
async def notify_off_cb(callback: CallbackQuery) -> None:
    """Отключить уведомления (очистить notify_chat_id)."""
    user_id = callback.from_user.id
    log.info("notify_off: user %s", user_id)

    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        if st:
            st.notify_chat_id = None
            await session.commit()

    await safe_edit(callback.message, "🔕 Notifications disabled.", reply_markup=notify_menu())
    await callback.answer()
