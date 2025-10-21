# app/routers/schedule_days.py
# ------------------------------------------------------------
# Пикер дней недели для шага добавления таймеров.
# Этот роутер дополняет существующий сценарий в app/routers/schedule.py:
#   • ожидаем состояние AddTimesFSM.waiting_days,
#   • даём переключать отдельные дни (Mon..Sun),
#   • поддерживаем пресеты: All / Weekdays / Weekends,
#   • по «✅ OK» сохраняем Job'ы с полем dow_mask и зовём reload_schedule().
#
# Ничего не ломаем: только добавляем шаг после парсинга строки
#  «09:00 | 12:30 :: Text». В самом schedule.py после успешного парсинга
#  нужно не создавать задачи сразу, а перейти в это состояние:
#
#     await state.update_data(add_times=times_list, add_text=text,
#                             add_account_id=account_id, dow_mask=127)
#     await safe_edit(message_or_cb_msg, 'Choose days...', reply_markup=dow_picker_kb(127))
#     await state.set_state(AddTimesFSM.waiting_days)
#     return
#
# После этого управление возьмут хендлеры из этого файла.
# ------------------------------------------------------------

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.fsm.context import FSMContext

from app.routers.schedule import AddTimesFSM   # используем состояние из твоего сценария
from app.database.models import async_session, Job
from app.keyboards import dow_picker_kb, schedule_menu
from app.services.safe_edit import safe_edit
from app.services.schedule_utils import all_days_mask, weekdays_mask, weekends_mask

router = Router(name="schedule_days")

# Переключение конкретного дня (0=Mon..6=Sun)
@router.callback_query(AddTimesFSM.waiting_days, F.data.startswith("dow_t:"))
async def dow_toggle_cb(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mask = int(data.get("dow_mask", 127))
    idx = int(cb.data.split(":")[1])
    mask ^= (1 << idx)  # переключаем бит
    await state.update_data(dow_mask=mask)
    await safe_edit(cb.message, "Choose days:", reply_markup=dow_picker_kb(mask))
    await cb.answer()

# Пресеты: All / Weekdays / Weekends
@router.callback_query(AddTimesFSM.waiting_days, F.data.in_(["dow_all", "dow_wkd", "dow_wke"]))
async def dow_presets_cb(cb: CallbackQuery, state: FSMContext):
    if cb.data == "dow_all":
        m = all_days_mask()
    elif cb.data == "dow_wkd":
        m = weekdays_mask()
    else:
        m = weekends_mask()
    await state.update_data(dow_mask=m)
    await safe_edit(cb.message, "Choose days:", reply_markup=dow_picker_kb(m))
    await cb.answer()

# Cancel → назад в меню Schedule
@router.callback_query(AddTimesFSM.waiting_days, F.data == "dow_cancel")
async def dow_cancel_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "Schedule menu:", reply_markup=schedule_menu())
    await cb.answer()

# OK → создаём Job'ы и пересобираем планировщик
@router.callback_query(AddTimesFSM.waiting_days, F.data == "dow_ok")
async def dow_ok_cb(cb: CallbackQuery, state: FSMContext):
    payload = await state.get_data()
    times      = payload.get("add_times", [])
    text       = payload.get("add_text", "")
    account_id = payload.get("add_account_id")
    mask       = int(payload.get("dow_mask", 127))
    user_id    = cb.from_user.id

    if not times or not text or not account_id:
        await state.clear()
        await safe_edit(cb.message, "Something went wrong. Try again.", reply_markup=schedule_menu())
        await cb.answer()
        return

    created = 0
    async with async_session() as session:
        for ts in times:
            session.add(Job(tg_user_id=user_id, time_str=ts, text=text, account_id=account_id, dow_mask=mask))
            created += 1
        await session.commit()

    # Пересобираем расписание
    from app.services.scheduler import reload_schedule
    active = await reload_schedule()

    await state.clear()
    await safe_edit(cb.message, f"✅ Added {created} timer(s).\nActive (all users): {active}", reply_markup=schedule_menu())
    await cb.answer()
