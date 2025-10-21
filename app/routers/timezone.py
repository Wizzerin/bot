# app/routers/timezone.py
# ------------------------------------------------------------
# Управление часовым поясом пользователя (Time Zone).
# ------------------------------------------------------------

from __future__ import annotations
import logging
from zoneinfo import available_timezones, ZoneInfo

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.database.models import async_session, BotSettings
from app.keyboards import tz_menu_kb, notify_menu
from app.services.safe_edit import safe_edit
from app.services.scheduler import reload_schedule

log = logging.getLogger(__name__)
router = Router(name="timezone")

# --- FSM для смены TZ ---
class TimezoneFSM(StatesGroup):
    waiting_tz = State()


@router.callback_query(F.data == "tz_menu")
async def tz_menu_open(cb: CallbackQuery, state: FSMContext) -> None:
    """Отображает меню с текущим часовым поясом."""
    await state.clear()
    async with async_session() as session:
        st = await session.get(BotSettings, cb.from_user.id)
    
    current_tz = st.tz if st and st.tz else "Europe/Berlin"
    
    await safe_edit(
        cb.message,
        f"Your current time zone is: <b>{current_tz}</b>\n\n"
        "This affects when your scheduled posts are published.",
        reply_markup=tz_menu_kb(current_tz)
    )
    await cb.answer()


@router.callback_query(F.data == "tz_change")
async def tz_change_start(cb: CallbackQuery, state: FSMContext) -> None:
    """Запрашивает новый часовой пояс."""
    await state.set_state(TimezoneFSM.waiting_tz)
    await safe_edit(
        cb.message,
        "Please send your time zone name (e.g., <code>America/New_York</code> or <code>UTC</code>).\n\n"
        "/cancel to abort.",
        reply_markup=None  # Убираем кнопки на время ввода
    )
    await cb.answer()


@router.message(TimezoneFSM.waiting_tz)
async def tz_set_handler(message: Message, state: FSMContext) -> None:
    """Принимает, валидирует и сохраняет новый часовой пояс."""
    tz_name = message.text.strip()
    
    # Валидация
    if tz_name not in available_timezones():
        await message.answer(
            f"❌ Time zone '<code>{tz_name}</code>' is not valid.\n"
            "Please try again (e.g., <code>Europe/Kyiv</code>) or /cancel."
        )
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

    # Перезагружаем планировщик с учётом нового TZ
    await reload_schedule()
    
    await state.clear()
    await message.answer(
        f"✅ Time zone updated to <b>{tz_name}</b>.",
        reply_markup=notify_menu()
    )
