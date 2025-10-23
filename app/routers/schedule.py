# app/routers/schedule.py
# ------------------------------------------------------------
# Раздел "⏱ Schedule" с интерактивным управлением задачами.
# ------------------------------------------------------------

from __future__ import annotations

import io
import csv
import logging
from html import escape
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile

from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.database.models import async_session, Job, Account, BotSettings, JobMedia
from app.keyboards import (
    schedule_menu, dow_picker_kb, weekly_view_kb, day_view_kb,
    job_list_kb, job_actions_kb, job_delete_confirm_kb
)
from app.services.safe_edit import safe_edit
from app.services.scheduler import reload_schedule
# (ИЗМЕНЕНО) Импорт _parse_hhmm теперь отсюда
from app.services.schedule_utils import mask_to_human, mask_to_days_label, parse_days_to_mask, _parse_hhmm

log = logging.getLogger(__name__)
router = Router()


# ---------- FSMs ----------
class AddTimesFSM(StatesGroup):
    waiting_account = State()
    waiting_pair    = State()
    waiting_days    = State()
    waiting_media   = State()

class EditJobFSM(StatesGroup):
    waiting_text = State()
    waiting_media = State()

class ClearAllFSM(StatesGroup):
    waiting_account = State()
    waiting_confirm = State()

class ImportCSVFSM(StatesGroup): # ВОССТАНОВЛЕНО
    waiting_doc = State()


# ---------- Вход в раздел ----------
@router.message(F.text == "⏱ Schedule")
async def sched_menu_open_reply(message: Message) -> None:
    await message.answer("Schedule menu:", reply_markup=schedule_menu())


@router.callback_query(F.data == "sched_menu")
async def sched_menu_open_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(cb.message, "Schedule menu:", reply_markup=schedule_menu())
    await cb.answer()


# =========================
#        ADD TIMES
# =========================
@router.callback_query(F.data == "sched_add")
async def sched_add_start(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id
    async with async_session() as session:
        accounts = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()

    if not accounts:
        await safe_edit(
            cb.message,
            "You have no accounts yet.\nAdd a token first: <code>Token → 🔑 Set token</code> or <code>/set_token TH...</code>",
            reply_markup=schedule_menu()
        )
        await cb.answer(); return

    if len(accounts) == 1:
        await state.update_data(account_id=accounts[0].id)
        await state.set_state(AddTimesFSM.waiting_pair)
        await safe_edit(
            cb.message,
            "Enter in format:\n<code>09:00 | 12:30 :: Post text</code>\n\n"
            f"• Account: <b>{escape(accounts[0].title or str(accounts[0].id))}</b>\n"
            "• Multiple times allowed using “|”\n• /cancel to cancel",
        )
        await cb.answer(); return

    await state.set_state(AddTimesFSM.waiting_account)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{a.title or 'untitled'} (id={a.id})", callback_data=f"sched_acc:{a.id}")]
        for a in accounts
    ] + [[InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu")]])
    await safe_edit(cb.message, "Choose an account for this schedule:", reply_markup=kb)
    await cb.answer()


@router.callback_query(AddTimesFSM.waiting_account, F.data.startswith("sched_acc:"))
async def sched_account_picked(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id
    try:
        acc_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer("Invalid account.", show_alert=True); return

    async with async_session() as session:
        acc = await session.get(Account, acc_id)

    if not acc or acc.tg_user_id != user_id:
        await cb.answer("Account not found.", show_alert=True); return

    await state.update_data(account_id=acc_id)
    await state.set_state(AddTimesFSM.waiting_pair)

    await safe_edit(
        cb.message,
        "Enter in format:\n<code>09:00 | 12:30 :: Post text</code>\n\n"
        f"• Account: <b>{escape(acc.title or str(acc.id))}</b>\n"
        "• Multiple times allowed using “|”\n• /cancel to cancel",
    )
    await cb.answer()


@router.message(AddTimesFSM.waiting_pair)
async def add_times_got_pair(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if "::" not in raw:
        await message.answer("❌ Format: <code>09:00 | 12:30 :: text</code>"); return

    times_part, text = [p.strip() for p in raw.split("::", 1)]
    if not text:
        await message.answer("❌ No text after '::'"); return

    times = []
    for t in [t.strip() for t in times_part.split("|") if t.strip()]:
        ok, _ = _parse_hhmm(t)
        if ok:
            times.append(t)
            
    if not times:
        await message.answer("❌ Bad time. Example: <code>09:00 | 12:30 :: text</code>"); return

    data = await state.get_data()
    account_id = data.get("account_id")
    if not account_id:
        await message.answer("⚠️ Pick an account first. Open Schedule again."); await state.clear(); return

    await state.update_data(add_times=times, add_text=text, add_account_id=account_id, dow_mask=127, images=[])
    await message.answer(
        "🗓 Choose days for: " + ", ".join(times) + "\n\nToggle days or presets.",
        reply_markup=dow_picker_kb(127)
    )
    await state.set_state(AddTimesFSM.waiting_days)


# ---- пикер дней ----
@router.callback_query(AddTimesFSM.waiting_days, F.data.startswith("dow_t:"))
async def dow_toggle_cb(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mask = int(data.get("dow_mask", 127))
    idx = int(cb.data.split(":")[1])
    mask ^= (1 << idx)
    await state.update_data(dow_mask=mask)
    await safe_edit(cb.message, cb.message.text, reply_markup=dow_picker_kb(mask))
    await cb.answer()


@router.callback_query(AddTimesFSM.waiting_days, F.data.in_(["dow_all", "dow_wkd", "dow_wke"]))
async def dow_presets_cb(cb: CallbackQuery, state: FSMContext):
    from app.services.schedule_utils import all_days_mask, weekdays_mask, weekends_mask
    if cb.data == "dow_all":
        m = all_days_mask()
    elif cb.data == "dow_wkd":
        m = weekdays_mask()
    else:
        m = weekends_mask()
    await state.update_data(dow_mask=m)
    await safe_edit(cb.message, cb.message.text, reply_markup=dow_picker_kb(m))
    await cb.answer()

# ---- После выбора дней -> переход к сбору медиа ----
@router.callback_query(AddTimesFSM.waiting_days, F.data == "dow_ok")
async def dow_ok_cb(cb: CallbackQuery, state: FSMContext):
    await state.set_state(AddTimesFSM.waiting_media)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Publish", callback_data="sched_publish"),
            InlineKeyboardButton(text="♻️ Clear Images", callback_data="sched_clear_img"),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu")],
    ])
    await safe_edit(
        cb.message,
        "Now send up to 10 photos, or press <b>Publish</b> to post without images.",
        reply_markup=kb
    )
    await cb.answer()


# ---- Сбор медиа и публикация ----

@router.message(AddTimesFSM.waiting_media, F.photo)
async def sched_take_photo(message: Message, state: FSMContext):
    """Собираем file_id фотографий в список в FSM."""
    data = await state.get_data()
    images: list[str] = data.get("images", [])

    if len(images) >= 10:
        await message.answer("You can only attach up to 10 photos.")
        return

    largest_photo = max(message.photo, key=lambda p: p.file_size or 0)
    images.append(largest_photo.file_id)
    await state.update_data(images=images)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Publish", callback_data="sched_publish"),
            InlineKeyboardButton(text="♻️ Clear Images", callback_data="sched_clear_img"),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu")],
    ])
    await message.answer(f"✅ Photo {len(images)}/10 added.", reply_markup=kb)


@router.callback_query(AddTimesFSM.waiting_media, F.data == "sched_clear_img")
async def sched_clear_images(cb: CallbackQuery, state: FSMContext):
    """Очищает список собранных фото."""
    await state.update_data(images=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Publish", callback_data="sched_publish"),
            InlineKeyboardButton(text="♻️ Clear Images", callback_data="sched_clear_img"),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu")],
    ])
    await safe_edit(cb.message, "Images cleared. You can now send new photos.", reply_markup=kb)
    await cb.answer()


@router.callback_query(AddTimesFSM.waiting_media, F.data == "sched_publish")
async def sched_publish_cb(cb: CallbackQuery, state: FSMContext) -> None:
    """Финальный шаг: создание Job и JobMedia в базе данных."""
    payload = await state.get_data()
    times = payload.get("add_times", [])
    text = payload.get("add_text", "")
    account_id = payload.get("add_account_id")
    mask = int(payload.get("dow_mask", 127))
    image_file_ids = payload.get("images", [])
    user_id = cb.from_user.id

    if not times or not text or not account_id:
        await state.clear()
        await safe_edit(cb.message, "Something went wrong. Try again.", reply_markup=schedule_menu())
        await cb.answer(); return

    created = 0
    async with async_session() as session:
        for ts in times:
            new_job = Job(
                tg_user_id=user_id, time_str=ts, text=text,
                account_id=account_id, dow_mask=mask
            )
            if image_file_ids:
                for file_id in image_file_ids:
                    new_job.media.append(JobMedia(source="telegram", tg_file_id=file_id))
            
            session.add(new_job)
            created += 1
        await session.commit()

    active = await reload_schedule()
    await state.clear()
    msg = f"✅ Added {created} timer(s)."
    if image_file_ids:
        msg += f" with {len(image_file_ids)} image(s)."
    msg += f"\nActive (all users): {active}"
    await safe_edit(cb.message, msg, reply_markup=schedule_menu())
    await cb.answer()


@router.message(AddTimesFSM.waiting_media)
async def sched_waiting_media_wrong(message: Message, state: FSMContext) -> None:
    await message.answer("Please send a *photo* (not a document). Or press Publish.")


@router.message(Command("cancel"), F.state.in_([AddTimesFSM, EditJobFSM, ImportCSVFSM]))
async def sched_cancel_any(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Canceled.", reply_markup=schedule_menu())


# ===============================================
#   LIST & УПРАВЛЕНИЕ КОНКРЕТНОЙ ЗАДАЧЕЙ
# ===============================================
@router.callback_query(F.data == "sched_list")
async def sched_list(cb: CallbackQuery) -> None:
    """Вместо текста, выводит задачи в виде кнопок."""
    user_id = cb.from_user.id
    async with async_session() as session:
        # (ИЗМЕНЕНИЕ) Используем selectinload для "жадной" загрузки связанных медиа-файлов,
        # чтобы избежать дополнительных запросов к БД в цикле.
        jobs = (await session.execute(
            select(Job)
            .where(Job.tg_user_id == user_id)
            .options(selectinload(Job.media)) # <-- Вот это изменение
            .order_by(Job.time_str, Job.id)
        )).scalars().all()

    if not jobs:
        await safe_edit(cb.message, "Your schedule is empty.", reply_markup=schedule_menu())
        await cb.answer(); return

    await safe_edit(cb.message, "Select a task to view or edit:", reply_markup=job_list_kb(jobs))
    await cb.answer()


@router.callback_query(F.data.startswith("sched_job_view:"))
async def sched_job_view(cb: CallbackQuery, state: FSMContext):
    """Показывает детали задачи и кнопки действий."""
    await state.clear()
    try:
        job_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid job ID.", show_alert=True)
        return

    async with async_session() as session:
        job = (await session.execute(
            select(Job).options(selectinload(Job.media)).where(Job.id == job_id)
        )).scalar_one_or_none()

    if not job or job.tg_user_id != cb.from_user.id:
        await cb.answer("Task not found.", show_alert=True)
        return

    dow_label = mask_to_human(getattr(job, "dow_mask", 127))
    media_count = len(job.media)

    text = (
        f"<b>Time:</b> {escape(job.time_str)}\n"
        f"<b>Days:</b> {escape(dow_label)}\n"
        f"<b>Images:</b> {media_count}\n\n"
        f"<b>Text:</b>\n{escape(job.text)}"
    )

    await safe_edit(cb.message, text, reply_markup=job_actions_kb(job_id))
    await cb.answer()


# ---- Удаление задачи ----
@router.callback_query(F.data.startswith("sched_job_delete:"))
async def sched_job_delete_confirm(cb: CallbackQuery):
    """Спрашивает подтверждение на удаление."""
    try:
        job_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid job ID.", show_alert=True)
        return
    await safe_edit(
        cb.message, "Are you sure you want to delete this scheduled post?",
        reply_markup=job_delete_confirm_kb(job_id)
    )
    await cb.answer()

@router.callback_query(F.data.startswith("sched_job_delete_confirm:"))
async def sched_job_delete_do(cb: CallbackQuery):
    """Выполняет удаление."""
    try:
        job_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid job ID.", show_alert=True)
        return
    
    async with async_session() as session:
        await session.execute(delete(Job).where(Job.id == job_id, Job.tg_user_id == cb.from_user.id))
        await session.commit()
    
    await reload_schedule()
    await cb.answer("Task deleted.")
    # Возвращаемся к обновленному списку
    await sched_list(cb)


# ---- Редактирование текста ----
@router.callback_query(F.data.startswith("sched_job_edit_text:"))
async def sched_job_edit_text_start(cb: CallbackQuery, state: FSMContext):
    try:
        job_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid job ID.", show_alert=True)
        return
    await state.set_state(EditJobFSM.waiting_text)
    await state.update_data(edit_job_id=job_id)
    await safe_edit(cb.message, "Please send the new text for this post.\n\n/cancel to abort.")
    await cb.answer()

@router.message(EditJobFSM.waiting_text)
async def sched_job_edit_text_finish(message: Message, state: FSMContext):
    new_text = (message.text or "").strip()
    if not new_text:
        await message.answer("Text cannot be empty. Please try again or /cancel.")
        return

    data = await state.get_data()
    job_id = data.get("edit_job_id")

    async with async_session() as session:
        job = await session.get(Job, job_id)
        if job and job.tg_user_id == message.from_user.id:
            job.text = new_text
            await session.commit()
    
    await state.clear()
    await message.answer("✅ Text updated.")
    
    # "Фальшивый" callback, чтобы вернуться к просмотру задачи
    fake_cb = type("C", (), {"data": f"sched_job_view:{job_id}", "message": message, "from_user": message.from_user, "answer": lambda: None})
    await sched_job_view(fake_cb, state)

# =========================
#      WEEKLY & DAY VIEW
# =========================
@router.callback_query(F.data == "sched_weekly_view")
async def sched_weekly_view(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        jobs = (await session.execute(
            select(Job).where(Job.tg_user_id == user_id)
        )).scalars().all()

    tz = ZoneInfo(st.tz) if st and getattr(st, "tz", None) else ZoneInfo("Europe/Berlin")
    now = datetime.now(tz)
    
    days_info = []
    
    if jobs:
        for i in range(7):
            current_day = now.date() + timedelta(days=i)
            dow = current_day.weekday()
            
            day_posts = 0
            for j in jobs:
                mask = getattr(j, "dow_mask", 127)
                if ((mask >> dow) & 1):
                    day_posts += 1
            
            date_str = current_day.strftime('%Y-%m-%d')
            label = current_day.strftime('%a, %b %d')
            days_info.append((label, date_str, day_posts))

    await safe_edit(
        cb.message,
        f"🗓️ <b>Weekly View</b>\nSelect a day to see details.\n\nTimezone: <b>{tz.key}</b>",
        reply_markup=weekly_view_kb(days_info)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("sched_day:"))
async def sched_day_view(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    try:
        date_str = cb.data.split(":", 1)[1]
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, IndexError):
        await cb.answer("Invalid date format.", show_alert=True)
        return

    async with async_session() as session:
        jobs = (await session.execute(
            select(Job).where(Job.tg_user_id == user_id).order_by(Job.time_str)
        )).scalars().all()
        
        acc_ids = {j.account_id for j in jobs}
        acc_map = {}
        if acc_ids:
            accs = (await session.execute(
                select(Account).where(Account.id.in_(acc_ids))
            )).scalars().all()
            acc_map = {a.id: (a.title or f"id={a.id}") for a in accs}

    dow = selected_date.weekday()
    
    lines = [f"🗓️ Posts for <b>{selected_date.strftime('%a, %b %d')}</b>:"]
    
    day_jobs = []
    for j in jobs:
        mask = getattr(j, "dow_mask", 127)
        if ((mask >> dow) & 1):
            day_jobs.append(j)

    if not day_jobs:
        lines.append("\nNo posts scheduled for this day.")
    else:
        for j in day_jobs:
            acc_label = acc_map.get(j.account_id, f"id={j.account_id}")
            short_text = (j.text or "")[:40]
            if len(j.text or "") > 40:
                short_text += "…"
            lines.append(f"• <b>{j.time_str}</b> - <i>{escape(short_text)}</i> (acc: {escape(acc_label)})")

    await safe_edit(
        cb.message,
        "\n".join(lines),
        reply_markup=day_view_kb(date_str)
    )
    await cb.answer()


# =========================
#         CLEAR ALL
# =========================
@router.callback_query(F.data == "sched_clear")
async def sched_clear_choose_scope(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id
    async with async_session() as session:
        accounts = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()

    await state.set_state(ClearAllFSM.waiting_account)
    rows = [[InlineKeyboardButton(text="All my accounts", callback_data="clr_acc:ALL")]] + [
        [InlineKeyboardButton(text=f"{a.title or 'untitled'} (id={a.id})", callback_data=f"clr_acc:{a.id}")]
        for a in accounts
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu")])
    await safe_edit(cb.message, "Choose scope to clear all timers:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()

@router.callback_query(ClearAllFSM.waiting_account, F.data.startswith("clr_acc:"))
async def sched_clear_confirm(cb: CallbackQuery, state: FSMContext) -> None:
    scope = cb.data.split(":", 1)[1]
    await state.update_data(clear_scope=scope)
    await state.set_state(ClearAllFSM.waiting_confirm)
    scope_label = "all accounts" if scope == "ALL" else f"account id={scope}"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Yes, clear for {scope_label}", callback_data="sched_clear_yes"),
        InlineKeyboardButton(text="↩️ Cancel", callback_data="sched_menu"),
    ]])
    await safe_edit(cb.message, f"Are you sure you want to clear timers for <b>{escape(scope_label)}</b>?", reply_markup=kb)
    await cb.answer()

@router.callback_query(ClearAllFSM.waiting_confirm, F.data == "sched_clear_yes")
async def sched_clear_do(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id
    scope = (await state.get_data()).get("clear_scope", "ALL")

    async with async_session() as session:
        q = select(Job).where(Job.tg_user_id == user_id)
        if scope != "ALL":
            q = q.where(Job.account_id == int(scope))
        rows = (await session.execute(q)).scalars().all()
        count_before = len(rows)
        if count_before:
            del_q = delete(Job).where(Job.tg_user_id == user_id)
            if scope != "ALL":
                del_q = del_q.where(Job.account_id == int(scope))
            await session.execute(del_q)
            await session.commit()

    active = await reload_schedule()
    await state.clear()
    scope_text = "all accounts" if scope == "ALL" else f"account id={scope}"
    await safe_edit(cb.message, f"🧹 Removed your timers: <b>{count_before}</b> (scope: <b>{escape(scope_text)}</b>).\n"
                                f"Active timers now (all users): <b>{active}</b>", reply_markup=schedule_menu())
    await cb.answer()


# ===============================================
#   (ВОССТАНОВЛЕНО) EXPORT & IMPORT
# ===============================================

def _uid_from_message(message_or_cb) -> int:
    """Корректно извлекает ID пользователя из Message или CallbackQuery."""
    event_from = getattr(message_or_cb, 'from_user', None)
    event_chat = getattr(message_or_cb, 'chat', None)
    
    if hasattr(message_or_cb, 'message') and message_or_cb.message: # Is CallbackQuery
        event_chat = getattr(message_or_cb.message, 'chat', None)
        
    try:
        if getattr(event_from, "is_bot", False):
            return event_chat.id
        return event_from.id
    except Exception:
        return event_chat.id


@router.callback_query(F.data == "sched_export")
async def export_schedule_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await export_schedule_cmd(cb.message)
    await cb.answer()


@router.message(Command("export_schedule"))
async def export_schedule_cmd_msg(message: Message):
    await export_schedule_cmd(message)


async def export_schedule_cmd(message: Message) -> None:
    uid = _uid_from_message(message)

    async with async_session() as session:
        jobs = (await session.execute(select(Job).where(Job.tg_user_id == uid))).scalars().all()
        if not jobs:
            await message.answer("Your schedule is empty.")
            return
        acc_ids = {j.account_id for j in jobs}
        acc_map = {
            a.id: (a.title or "")
            for a in (await session.execute(select(Account).where(Account.id.in_(acc_ids)))).scalars().all()
        }

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_str", "account_id", "account_title", "text", "dow_mask", "days"])
    for j in jobs:
        mask = getattr(j, "dow_mask", 127)
        w.writerow([j.time_str, j.account_id, acc_map.get(j.account_id, ""), j.text, mask, mask_to_days_label(mask)])
    data = buf.getvalue().encode("utf-8")

    await message.answer_document(
        BufferedInputFile(data, filename="schedule_export.csv"),
        caption="📤 Exported your schedule."
    )


@router.callback_query(F.data == "sched_import")
async def import_schedule_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(
        cb.message,
        "📥 Send a CSV file with header at least: <code>time_str,account_id,text</code>\n"
        "Optional: <code>dow_mask</code> or <code>days</code> (Daily/Weekdays/Mon,Wed,...)",
        reply_markup=schedule_menu()
    )
    await state.set_state(ImportCSVFSM.waiting_doc)
    await cb.answer()

@router.message(Command("import_schedule"))
async def import_schedule_cmd_msg(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📥 Send a CSV file with header at least: <code>time_str,account_id,text</code>\n"
        "Optional: <code>dow_mask</code> or <code>days</code> (Daily/Weekdays/Mon,Wed,...)"
    )
    await state.set_state(ImportCSVFSM.waiting_doc)


@router.message(ImportCSVFSM.waiting_doc, F.document)
async def import_schedule_receive_doc(message: Message, state: FSMContext) -> None:
    file = message.document
    if not file.file_name.lower().endswith(".csv"):
        await message.answer("File must be .csv")
        return

    uid = _uid_from_message(message)
    async with async_session() as session:
        accs = (await session.execute(select(Account).where(Account.tg_user_id == uid))).scalars().all()
        if not accs:
            await state.clear()
            await message.answer("You have no accounts. Add a token first.")
            return
        default_acc_id = accs[0].id

    try:
        file_bytes = await message.bot.download(file)
        content = (await file_bytes.read()).decode("utf-8", errors="replace")
    except Exception:
        await message.answer("Failed to read the file. Try again.")
        return

    added, errors = 0, 0
    async with async_session() as session:
        reader = csv.DictReader(io.StringIO(content))
        fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
        has_mask, has_days = "dow_mask" in fieldnames, "days" in fieldnames

        for row in reader:
            ts = (row.get("time_str") or "").strip()
            ok, _ = _parse_hhmm(ts)
            if not ok: errors += 1; continue
            
            text = (row.get("text") or "").strip()
            if not text: errors += 1; continue
            
            acc_id = (row.get("account_id") or "").strip()
            acc_id = int(acc_id) if acc_id.isdigit() else default_acc_id

            mask = 127
            if has_mask and (raw_mask := (row.get("dow_mask") or "").strip()).isdigit():
                mask = int(raw_mask)
            elif has_days and (parsed_mask := parse_days_to_mask(row.get("days"))) is not None:
                mask = parsed_mask
            
            session.add(Job(tg_user_id=uid, time_str=ts, text=text, account_id=acc_id, dow_mask=mask))
            added += 1

        await session.commit()

    await state.clear()
    active = await reload_schedule()
    await message.answer(
        f"📥 Imported {added} row(s), skipped {errors}. Active timers: {active}",
        reply_markup=schedule_menu()
    )
