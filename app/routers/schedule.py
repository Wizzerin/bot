# app/routers/schedule.py
# ------------------------------------------------------------
# Раздел "⏱ Schedule" с выбором дней недели.
# Важно: нигде не дёргаем ленивые отношения SQLAlchemy (media и т.п.) —
# в списке тоже, чтобы не ловить DetachedInstanceError.
# ------------------------------------------------------------

from __future__ import annotations

import logging
from html import escape
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from sqlalchemy import select, delete

from app.database.models import async_session, Job, Account, BotSettings
from app.keyboards import schedule_menu, dow_picker_kb
from app.services.safe_edit import safe_edit
from app.services.scheduler import reload_schedule
from app.services.schedule_utils import mask_to_human, mask_to_cron  # <— добавили mask_to_cron
# добавим доступ к ре-хостингу изображений (публичные URL для Threads)
from app.services import tg_io

log = logging.getLogger(__name__)
router = Router()

IMG_MARKER = "[IMG]"  # маркер для встраивания ссылки на картинку в текст

# ---------- FSM: добавление ----------
class AddTimesFSM(StatesGroup):
    waiting_account = State()   # выбор аккаунта (если их > 1)
    waiting_pair    = State()   # ввод "09:00 | 12:30 :: текст"
    waiting_days    = State()   # выбор дней
    ask_image       = State()   # НОВОЕ: спросить, нужна ли картинка
    waiting_image   = State()   # НОВОЕ: ожидание фото

# ---------- FSM: удаление по HH:MM ----------
class RemoveTimeFSM(StatesGroup):
    waiting_account = State()
    waiting_time    = State()

# ---------- FSM: очистка всех ----------
class ClearAllFSM(StatesGroup):
    waiting_account = State()
    waiting_confirm = State()

# ---------- Вход в раздел ----------
@router.message(F.text == "⏱ Schedule")
async def sched_menu_open_reply(message: Message) -> None:
    await message.answer("Schedule menu:", reply_markup=schedule_menu())

@router.callback_query(F.data == "sched_menu")
async def sched_menu_open_cb(cb: CallbackQuery) -> None:
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
            reply_markup=schedule_menu()
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
        reply_markup=schedule_menu()
    )
    await cb.answer()

@router.message(AddTimesFSM.waiting_pair)
async def add_times_got_pair(message: Message, state: FSMContext):
    """
    Пример строки: 09:00 | 12:30 :: Текст
    На этом шаге сохраняем times/text и открываем выбор дней.
    """
    raw = (message.text or "").strip()
    if "::" not in raw:
        await message.answer("❌ Format: <code>09:00 | 12:30 :: text</code>"); return

    times_part, text = [p.strip() for p in raw.split("::", 1)]
    if not text:
        await message.answer("❌ No text after '::'"); return

    # Разбираем времена
    times = []
    for t in [t.strip() for t in times_part.split("|") if t.strip()]:
        if len(t) == 5 and t[2] == ":" and t[:2].isdigit() and t[3:].isdigit():
            hh, mm = int(t[:2]), int(t[3:])
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                times.append(f"{hh:02d}:{mm:02d}")
    if not times:
        await message.answer("❌ Bad time. Example: <code>09:00 | 12:30 :: text</code>"); return

    data = await state.get_data()
    account_id = data.get("account_id")
    if not account_id:
        await message.answer("⚠️ Pick an account first. Open Schedule again."); await state.clear(); return

    await state.update_data(add_times=times, add_text=text, add_account_id=account_id, dow_mask=127)
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
    await safe_edit(cb.message, "Choose days:", reply_markup=dow_picker_kb(mask))
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
    await safe_edit(cb.message, "Choose days:", reply_markup=dow_picker_kb(m))
    await cb.answer()

@router.callback_query(AddTimesFSM.waiting_days, F.data == "dow_cancel")
async def dow_cancel_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "Schedule menu:", reply_markup=schedule_menu())
    await cb.answer()

# ---- подтверждение выбора дней → СПРОСИТЬ ПРО КАРТИНКУ ----
@router.callback_query(AddTimesFSM.waiting_days, F.data == "dow_ok")
async def dow_ok_cb(cb: CallbackQuery, state: FSMContext):
    # Вместо немедленного создания задач — спросим, нужна ли картинка
    await state.set_state(AddTimesFSM.ask_image)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Add image", callback_data="sched_img_yes"),
         InlineKeyboardButton(text="✖️ No image", callback_data="sched_img_no")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu")],
    ])
    await safe_edit(cb.message, "Add an image to this scheduled post?", reply_markup=kb)
    await cb.answer()

@router.callback_query(AddTimesFSM.ask_image, F.data == "sched_img_no")
async def sched_img_no(cb: CallbackQuery, state: FSMContext) -> None:
    # создаём задачи как раньше (без картинки)
    payload = await state.get_data()
    times = payload.get("add_times", [])
    text = payload.get("add_text", "")
    account_id = payload.get("add_account_id")
    mask = int(payload.get("dow_mask", 127))
    user_id = cb.from_user.id

    if not times or not text or not account_id:
        await state.clear()
        await safe_edit(cb.message, "Something went wrong. Try again.", reply_markup=schedule_menu())
        await cb.answer(); return

    created = 0
    async with async_session() as session:
        for ts in times:
            session.add(Job(tg_user_id=user_id, time_str=ts, text=text, account_id=account_id, dow_mask=mask))
            created += 1
        await session.commit()

    active = await reload_schedule()
    await state.clear()
    await safe_edit(cb.message, f"✅ Added {created} timer(s).\nActive (all users): {active}", reply_markup=schedule_menu())
    await cb.answer()

@router.callback_query(AddTimesFSM.ask_image, F.data == "sched_img_yes")
async def sched_img_yes(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddTimesFSM.waiting_image)
    await safe_edit(cb.message, "Please send a photo now.\n\n/cancel to abort", reply_markup=None)
    await cb.answer()

@router.message(AddTimesFSM.waiting_image, F.photo)
async def sched_take_photo(message: Message, state: FSMContext) -> None:
    """
    Получаем фото → строим публичный URL (через tg_io) → добавляем к тексту маркером [IMG] <url>
    → создаём задачи.
    """
    photo = message.photo[-1]
    try:
        image_url = await tg_io.build_public_url(photo.file_id)
    except Exception as e:
        await message.answer(f"Failed to process image: {e}\nSend another photo or /cancel.")
        return

    payload = await state.get_data()
    times = payload.get("add_times", [])
    text = payload.get("add_text", "")
    account_id = payload.get("add_account_id")
    mask = int(payload.get("dow_mask", 127))
    user_id = message.from_user.id

    if not times or not text or not account_id:
        await state.clear()
        await message.answer("Something went wrong. Try again.", reply_markup=schedule_menu())
        return

    # аккуратно добавим маркер с URL в конец текста
    text_with_img = f"{text}\n\n{IMG_MARKER} {image_url}"

    created = 0
    async with async_session() as session:
        for ts in times:
            session.add(Job(tg_user_id=user_id, time_str=ts, text=text_with_img, account_id=account_id, dow_mask=mask))
            created += 1
        await session.commit()

    active = await reload_schedule()
    await state.clear()
    await message.answer(f"✅ Added {created} timer(s) with image.\nActive (all users): {active}", reply_markup=schedule_menu())

@router.message(AddTimesFSM.waiting_image)
async def sched_waiting_image_wrong(message: Message, state: FSMContext) -> None:
    await message.answer("Please send a *photo* (not a document). Or /cancel to abort.")

@router.message(AddTimesFSM.waiting_image, Command("cancel"))
async def sched_cancel_image(message: Message, state: FSMContext) -> None:
    # если отменили на шаге картинки — вернёмся к меню без создания задач
    await state.clear()
    await message.answer("Canceled.", reply_markup=schedule_menu())

# =========================
#           LIST
# =========================
@router.callback_query(F.data == "sched_list")
async def sched_list(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    async with async_session() as session:
        rows = (await session.execute(
            select(Job).where(Job.tg_user_id == user_id).order_by(Job.time_str, Job.id)
        )).scalars().all()

        acc_ids = {r.account_id for r in rows}
        acc_map = {}
        if acc_ids:
            accs = (await session.execute(
                select(Account).where(Account.id.in_(acc_ids))
            )).scalars().all()
            acc_map = {a.id: (a.title or f"id={a.id}") for a in accs}

    if not rows:
        await safe_edit(cb.message, "Your schedule is empty.", reply_markup=schedule_menu())
        await cb.answer(); return

    lines = []
    for r in rows:
        acc_label = acc_map.get(r.account_id, f"id={r.account_id}")
        dow_label = mask_to_human(getattr(r, "dow_mask", 127))  # e.g. Daily / Mon,Wed,Fri
        short = (r.text or "")[:80]
        if len(r.text or "") > 80:
            short += "…"
        lines.append(f"• {escape(r.time_str)} — {escape(short)}  <i>({escape(dow_label)}; acc: {escape(acc_label)})</i>")

    await safe_edit(cb.message, "<b>Tasks:</b>\n" + "\n".join(lines), reply_markup=schedule_menu())
    await cb.answer()

# =========================
#      NEXT 7 DAYS (фикс дублей)
# =========================
@router.callback_query(F.data == "sched_next7")
async def sched_next7(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id

    # забираем TZ и все задания пользователя
    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        jobs = (await session.execute(
            select(Job).where(Job.tg_user_id == user_id).order_by(Job.time_str)
        )).scalars().all()

    tz = ZoneInfo(st.tz) if st and getattr(st, "tz", None) else ZoneInfo("Europe/Berlin")
    now = datetime.now(tz)
    end = now + timedelta(days=7)

    def _parse_hhmm(hhmm: str) -> tuple[int, int]:
        return int(hhmm[:2]), int(hhmm[3:])

    DOW_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    seen = set()        # (date, "HH:MM", account_id)
    hits: list[tuple[datetime, str]] = []

    cur = now.date()
    while cur <= end.date():
        dow = cur.weekday()  # 0=Mon
        for j in jobs:
            mask = getattr(j, "dow_mask", 127)
            if ((mask >> dow) & 1) == 0:
                continue
            try:
                h, m = _parse_hhmm(j.time_str)
            except Exception:
                continue

            dt = datetime(cur.year, cur.month, cur.day, h, m, tzinfo=tz)
            if dt < now:
                continue

            key = (dt.date(), j.time_str, j.account_id)
            if key in seen:
                continue
            seen.add(key)

            line = f"• {dt:%a %d.%m %H:%M} — {j.time_str} ({DOW_SHORT[dow]}; acc id={j.account_id})"
            hits.append((dt, line))
        cur += timedelta(days=1)

    hits.sort(key=lambda x: x[0])

    lines = [
        f"Timezone: <b>{tz.key}</b>",
        "Next triggers (7 days):",
    ]
    if hits:
        lines += [s for _, s in hits]
    else:
        lines.append("— no upcoming triggers —")

    await safe_edit(cb.message, "\n".join(lines), reply_markup=schedule_menu())
    await cb.answer()

# =========================
#      REMOVE BY TIME
# =========================
@router.callback_query(F.data == "sched_remove")
async def sched_remove_start(cb: CallbackQuery, state: FSMContext) -> None:
    user_id = cb.from_user.id
    async with async_session() as session:
        accounts = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()

    await state.set_state(RemoveTimeFSM.waiting_account)
    rows = [[InlineKeyboardButton(text="All my accounts", callback_data="rem_acc:ALL")]] + [
        [InlineKeyboardButton(text=f"{a.title or 'untitled'} (id={a.id})", callback_data=f"rem_acc:{a.id}")]
        for a in accounts
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu")])
    await safe_edit(cb.message, "Choose scope for removal:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()

@router.callback_query(RemoveTimeFSM.waiting_account, F.data.startswith("rem_acc:"))
async def sched_remove_account_chosen(cb: CallbackQuery, state: FSMContext) -> None:
    scope = cb.data.split(":", 1)[1]
    await state.update_data(remove_scope=scope)
    await state.set_state(RemoveTimeFSM.waiting_time)
    scope_label = "all accounts" if scope == "ALL" else f"account id={scope}"
    await safe_edit(cb.message, f"Send a time <code>HH:MM</code> to remove for <b>{escape(scope_label)}</b>.",
                    reply_markup=schedule_menu())
    await cb.answer()

@router.message(RemoveTimeFSM.waiting_time)
async def sched_remove_handle(message: Message, state: FSMContext) -> None:
    t = (message.text or "").strip()
    if len(t) != 5 or t[2] != ":" or not t[:2].isdigit() or not t[3:].isdigit():
        await message.answer(f"Invalid time format: <code>{escape(t)}</code>"); return

    user_id = message.from_user.id
    data = await state.get_data()
    scope = data.get("remove_scope", "ALL")

    async with async_session() as session:
        q = select(Job).where(Job.tg_user_id == user_id, Job.time_str == t)
        if scope != "ALL":
            q = q.where(Job.account_id == int(scope))
        rows = (await session.execute(q)).scalars().all()
        removed = len(rows)
        if removed:
            del_q = delete(Job).where(Job.tg_user_id == user_id, Job.time_str == t)
            if scope != "ALL":
                del_q = del_q.where(Job.account_id == int(scope))
            await session.execute(del_q)
            await session.commit()

    active = await reload_schedule()
    await state.clear()

    if removed:
        await message.answer(
            f"🗑️ Removed {removed} at <b>{escape(t)}</b> for <b>{'all accounts' if scope=='ALL' else 'account id='+scope}</b>.",
            reply_markup=schedule_menu()
        )
    else:
        await message.answer(f"No tasks at <b>{escape(t)}</b> for selected scope.", reply_markup=schedule_menu())

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

# ===== CLI (как было) =====
@router.message(Command("add_times"))
async def add_times_cli(message: Message, state: FSMContext) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or "::" not in parts[1]:
        await message.answer("Format:\n<code>/add_times 09:00 | 12:00 :: Post text</code>")
        return

    left, text = [x.strip() for x in parts[1].split("::", 1)]
    times = [t.strip() for t in left.split("|") if t.strip()]
    for t in times:
        if len(t) != 5 or t[2] != ":" or not t[:2].isdigit() or not t[3:].isdigit():
            await message.answer(f"Invalid time format: <code>{escape(t)}</code>")
            return

    user_id = message.from_user.id
    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        acc_id = st.default_account_id if st and st.default_account_id else None
        if acc_id is None:
            first_acc = (await session.execute(
                select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
            )).scalars().first()
            if not first_acc:
                await message.answer("Add a token first: /set_token THAA...")
                return
            acc_id = first_acc.id
        for t in times:
            session.add(Job(tg_user_id=user_id, time_str=t, text=text, account_id=acc_id, dow_mask=127))
        await session.commit()

    active = await reload_schedule()
    async with async_session() as session:
        my_jobs = (await session.execute(
            select(Job).where(Job.tg_user_id == user_id)
        )).scalars().all()
    await message.answer(f"✅ Added {len(times)}. Your active tasks: <b>{len(my_jobs)}</b> (all users: {active})")

@router.message(Command("list_times"))
async def list_times_cli(message: Message) -> None:
    cb = type("C", (), {"from_user": message.from_user, "message": message})
    await sched_list(cb)

@router.message(Command("remove_time"))
async def remove_time_cli(message: Message, state: FSMContext) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Format: <code>/remove_time 12:00</code>")
        return
    t = parts[1].strip()
    await state.update_data(remove_scope="ALL")
    await RemoveTimeFSM.waiting_time.set(state)
    await sched_remove_handle(message, state)

@router.message(Command("clear_times"))
async def clear_times_cli(message: Message) -> None:
    user_id = message.from_user.id
    async with async_session() as session:
        rows = (await session.execute(
            select(Job).where(Job.tg_user_id == user_id)
        )).scalars().all()
        count_before = len(rows)
        if count_before == 0:
            await message.answer("The list is empty.")
            return
        await session.execute(delete(Job).where(Job.tg_user_id == user_id))
        await session.commit()

    active = await reload_schedule()
    await message.answer(
        f"🧹 Your timers removed: <b>{count_before}</b>.\n"
        f"Active timers now (all users): <b>{active}</b>"
    )
