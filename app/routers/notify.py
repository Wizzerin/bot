# app/routers/notify.py
# ------------------------------------------------------------
# Меню уведомлений и смена часового пояса
# ------------------------------------------------------------

from __future__ import annotations
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select

from app.keyboards import notify_menu, tz_menu
from app.database.models import async_session, BotSettings, Job
from app.services.notifications import notify_user
from app.services.safe_edit import safe_edit
from app.services.scheduler import reload_schedule

router = Router()
log = logging.getLogger(__name__)

# ---------- FSM для смены Time Zone ----------
class TimezoneFSM(StatesGroup):
    waiting_tz = State()

# ---------- Уведомления ----------

@router.callback_query(F.data == "notify_menu")
async def notify_menu_open(callback: CallbackQuery) -> None:
    log.info("notify_menu: open by user %s", callback.from_user.id)
    await safe_edit(callback.message, "Scheduler notifications:", reply_markup=notify_menu())
    await callback.answer()

@router.callback_query(F.data == "notify_here")
async def notify_here_cb(callback: CallbackQuery) -> None:
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
    user_id = callback.from_user.id
    log.info("notify_test: user %s", user_id)
    await notify_user(user_id, "🧪 Test notification from scheduler (button)")
    await callback.answer("Sent test message if chat is configured.")

@router.callback_query(F.data == "notify_off")
async def notify_off_cb(callback: CallbackQuery) -> None:
    user_id = callback.from_user.id
    log.info("notify_off: user %s", user_id)

    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        if st:
            st.notify_chat_id = None
            await session.commit()

    await safe_edit(callback.message, "🔕 Notifications disabled.", reply_markup=notify_menu())
    await callback.answer()

# ---------- Time Zone ----------

async def get_user_tz(user_id: int, session: async_session) -> str:
    st = await session.get(BotSettings, user_id)
    return st.tz if st and st.tz else "Europe/Berlin"

@router.callback_query(F.data == "tz_menu")
async def tz_menu_open_cb(cb: CallbackQuery):
    async with async_session() as session:
        current_tz = await get_user_tz(cb.from_user.id, session)
    await safe_edit(cb.message, "Time Zone Settings:", reply_markup=tz_menu(current_tz))
    await cb.answer()

@router.callback_query(F.data == "tz_enter")
async def tz_enter(cb: CallbackQuery, state: FSMContext):
    await state.set_state(TimezoneFSM.waiting_tz)
    await safe_edit(
        cb.message,
        "Please send your time zone in <code>Area/City</code> format (e.g., <code>Europe/Kyiv</code>, <code>America/New_York</code>, <code>UTC</code>).\n\n"
        "/cancel to abort.",
        reply_markup=None
    )
    await cb.answer()

@router.message(TimezoneFSM.waiting_tz)
async def tz_enter_handler(message: Message, state: FSMContext):
    tz_name = message.text.strip()
    try:
        ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        await message.answer("❌ Invalid time zone name. Please use the <code>Area/City</code> format or /cancel.")
        return

    user_id = message.from_user.id
    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        if not st:
            st = BotSettings(tg_user_id=user_id, tz=tz_name)
            session.add(st)
        else:
            st.tz = tz_name
        await session.commit()
    
    await reload_schedule()
    await state.clear()
    await message.answer(f"✅ Time zone set to <b>{tz_name}</b>.", reply_markup=notify_menu())

