# app/routers/schedule_tools.py
# ------------------------------------------------------------
# Schedule Tools: Export / Import / Next runs (7 days)
# Обновлено: экспортирует dow_mask и человекочитаемые days,
# импорт принимает либо dow_mask, либо days (Daily/Weekdays/Weekends/Mon,Wed,...).
# ------------------------------------------------------------

from __future__ import annotations

import io
import csv
from datetime import datetime, timedelta
from typing import Tuple, Iterable

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy import select

from app.database.models import async_session, Account, Job, BotSettings
from app.services.scheduler import reload_schedule
from app.services.safe_edit import safe_edit
from app.keyboards import schedule_menu
from app.services.schedule_utils import (
    all_days_mask, weekdays_mask, weekends_mask,
)

router = Router(name="schedule_tools")


# ---------- ВСПОМОГАТЕЛЬНОЕ ----------

def _parse_hhmm(s: str) -> Tuple[bool, str | None]:
    try:
        hh, mm = s.strip().split(":")
        hh_i, mm_i = int(hh), int(mm)
        if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
            return True, None
    except Exception:
        pass
    return False, "Time must be HH:MM (00-23:00-59)."


async def _get_tz_for_user(tg_user_id: int) -> str:
    async with async_session() as session:
        st = (await session.execute(
            select(BotSettings).where(BotSettings.tg_user_id == tg_user_id)
        )).scalars().first()
        return (st.tz if st and st.tz else "Europe/Berlin")


def _next_run_from_time_str(time_str: str, tz_name: str) -> datetime:
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        from pytz import timezone as _tz
        tz = _tz(tz_name)
    now = datetime.now(tz)
    hh, mm = map(int, time_str.split(":"))
    dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if dt <= now:
        dt += timedelta(days=1)
    return dt


# --- days <-> mask ---

_SHORT2IDX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_PRINT_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def mask_to_days_label(mask: int) -> str:
    """127 → Daily; будни/выходные → пресеты; иначе перечисление Mon,Wed,..."""
    if mask is None or mask == 127:
        return "Daily"
    if mask == weekdays_mask():   # Пн–Пт
        return "Weekdays"
    if mask == weekends_mask():   # Сб–Вс
        return "Weekends"
    parts = [_PRINT_NAMES[i] for i in range(7) if (mask >> i) & 1]
    return ",".join(parts) if parts else "—"

def parse_days_to_mask(value: str | None) -> int | None:
    """
    days → dow_mask.
    Поддерживает: Daily / Weekdays / Weekends / Mon,Wed,Fri (регистр не важен, пробелы можно).
    Возврат None → нет информации (пусть решает вызывающий код).
    """
    if not value:
        return None
    s = value.strip().lower()
    if not s:
        return None
    # presets
    if s in ("daily",):
        return 127
    if s in ("weekdays", "wd", "workdays"):
        return weekdays_mask()
    if s in ("weekends", "we"):
        return weekends_mask()
    # mon,wed,fri
    items = [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]
    mask = 0
    for it in items:
        idx = _SHORT2IDX.get(it[:3])  # допускаем "monday" → "mon"
        if idx is None:
            return None  # неизвестное слово → пусть вызывающий решит
        mask |= (1 << idx)
    return mask or None


# ---------- FSM для inline-импорта ----------

class ImportCSV(StatesGroup):
    # Состояние ожидания документа CSV после нажатия кнопки/команды импорта
    waiting_doc = State()


def _uid_from_message(message) -> int:
    """
    Корректно берём uid:
    - для команд: message.from_user.id (это пользователь)
    - для inline-кнопок (message прислал бот): берём message.chat.id (в личке = uid)
    """
    try:
        if getattr(message.from_user, "is_bot", False):
            return message.chat.id
        return message.from_user.id
    except Exception:
        # на всякий случай
        return message.chat.id


# ---------- КОМАНДЫ (каждая очищает FSM перед действием) ----------

@router.message(Command("export_schedule"), StateFilter("*"))
async def export_schedule_cmd(message: Message, state: FSMContext) -> None:
    """
    Экспорт расписания в CSV со столбцами:
    time_str,account_id,account_title,text,dow_mask,days
    """
    await state.clear()
    uid = _uid_from_message(message)

    async with async_session() as session:
        rows = (await session.execute(select(Job).where(Job.tg_user_id == uid))).scalars().all()
        if not rows:
            await message.answer("Your schedule is empty.")
            return
        acc_ids = {r.account_id for r in rows}
        acc_map = {
            a.id: (a.title or "")
            for a in (await session.execute(select(Account).where(Account.id.in_(acc_ids)))).scalars().all()
        }

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_str", "account_id", "account_title", "text", "dow_mask", "days"])
    for j in rows:
        mask = getattr(j, "dow_mask", 127)
        w.writerow([j.time_str, j.account_id, acc_map.get(j.account_id, ""), j.text, mask, mask_to_days_label(mask)])
    data = buf.getvalue().encode("utf-8")

    await message.answer_document(
        BufferedInputFile(data, filename="schedule_export.csv"),
        caption="📤 Exported your schedule."
    )


@router.message(Command("import_schedule"), StateFilter("*"))
async def import_schedule_cmd(message: Message, state: FSMContext) -> None:
    """Старт импорта: просим CSV и ставим состояние ожидания файла."""
    await state.clear()
    await message.answer(
        "📥 Send a CSV file with header at least:\n"
        "<code>time_str,account_id,account_title,text</code>\n\n"
        "Optional columns:\n"
        "• <code>dow_mask</code> — integer bitmask (127=daily)\n"
        "• <code>days</code> — e.g. <i>Daily</i>, <i>Weekdays</i>, <i>Weekends</i>, <i>Mon,Wed,Fri</i>\n"
        "If both present, <b>dow_mask</b> wins."
    )
    await state.set_state(ImportCSV.waiting_doc)


@router.message(ImportCSV.waiting_doc, F.document)
async def import_schedule_receive_doc(message: Message, state: FSMContext) -> None:
    """
    Принимаем CSV, создаём Job:
      • time_str: HH:MM
      • text: not empty
      • account_id: если пусто — берём первый аккаунт пользователя
      • dow_mask: из колонки dow_mask (int) или days (строка)
    """
    file = message.document
    if not file.file_name.lower().endswith(".csv"):
        await message.answer("File must be .csv")
        return

    uid = message.from_user.id
    async with async_session() as session:
        accs = (await session.execute(select(Account).where(Account.tg_user_id == uid))).scalars().all()
        if not accs:
            await state.clear()
            await message.answer("You have no accounts. Add a token first.")
            return
        default_acc_id = accs[0].id

    # читаем файл
    try:
        file_bytes = await message.bot.download(file)
        content = (await file_bytes.read()).decode("utf-8", errors="replace")
    except Exception:
        await message.answer("Failed to read the file. Try again.")
        return

    added = 0
    errors = 0
    async with async_session() as session:
        reader = csv.DictReader(io.StringIO(content))
        # нормализуем имена колонок
        fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
        has_mask = "dow_mask" in fieldnames
        has_days = "days" in fieldnames

        for row in reader:
            # базовые поля
            ts = (row.get("time_str") or row.get("Time") or "").strip()
            ok, _ = _parse_hhmm(ts)
            if not ok:
                errors += 1
                continue

            text = (row.get("text") or row.get("Text") or "").strip()
            if not text:
                errors += 1
                continue

            # account_id
            acc_id_str = (row.get("account_id") or row.get("Account") or "").strip()
            acc_id = int(acc_id_str) if acc_id_str.isdigit() else default_acc_id

            # mask: приоритет dow_mask → days → default(127)
            mask = 127
            if has_mask:
                raw = (row.get("dow_mask") or "").strip()
                if raw.isdigit():
                    try:
                        mask = int(raw)
                    except Exception:
                        pass
            if has_days and (not has_mask or mask == 127):
                parsed = parse_days_to_mask(row.get("days"))
                if parsed is not None:
                    mask = parsed

            session.add(Job(tg_user_id=uid, time_str=ts, text=text, account_id=acc_id, dow_mask=mask))
            added += 1

        await session.commit()

    await state.clear()
    active = await reload_schedule()
    await message.answer(
        f"📥 Imported {added} row(s), skipped {errors}. Active timers: {active}",
        reply_markup=schedule_menu()
    )


# ---------- NEXT RUNS (7 дней вперёд, с учётом маски) ----------

@router.message(Command("next_runs"), StateFilter("*"))
async def next_runs_cmd(message: Message, state: FSMContext) -> None:
    """
    Показывает ближайшие срабатывания таймеров пользователя на 7 дней вперёд
    (учитывает личную TZ и dow_mask). Выводим до 20 ближайших.
    """
    await state.clear()
    uid = _uid_from_message(message)

    # tz + все задачи пользователя
    async with async_session() as session:
        st = (await session.execute(select(BotSettings).where(BotSettings.tg_user_id == uid))).scalars().first()
        tz_name = (st.tz if st and st.tz else "Europe/Berlin")
        rows = (await session.execute(select(Job).where(Job.tg_user_id == uid))).scalars().all()

    if not rows:
        await message.answer("Your schedule is empty.")
        return

    def next_dates_for_job(time_str: str, mask: int, tz: str, days: int = 7) -> Iterable[datetime]:
        try:
            import zoneinfo
            zone = zoneinfo.ZoneInfo(tz)
        except Exception:
            from pytz import timezone as _tz
            zone = _tz(tz)
        hh, mm = map(int, time_str.split(":"))
        now = datetime.now(zone).replace(second=0, microsecond=0)
        cur = now
        for _ in range(days):
            dt = cur.replace(hour=hh, minute=mm)
            if dt < cur:
                dt += timedelta(days=1)
            # ищем ближайший разрешённый день
            while True:
                wd = dt.weekday()  # Mon=0..Sun=6
                if (mask >> wd) & 1:
                    yield dt
                    break
                dt += timedelta(days=1)
            cur += timedelta(days=1)

    upcoming: list[tuple[datetime, Job]] = []
    for j in rows:
        m = getattr(j, "dow_mask", 127)
        upcoming.extend((dt, j) for dt in next_dates_for_job(j.time_str, m, tz_name, days=7))

    upcoming.sort(key=lambda x: x[0])
    upcoming = upcoming[:20]

    lines = [f"Timezone: <b>{tz_name}</b>", "Next triggers (7 days):"]
    for dt, j in upcoming:
        lines.append(f"• {dt:%a %d.%m %H:%M} — {j.time_str} ({mask_to_days_label(getattr(j, 'dow_mask', 127))}; acc id={j.account_id})")

    await message.answer("\n".join(lines), reply_markup=schedule_menu())


# ---------- INLINE КНОПКИ В ⏱ Schedule ----------

@router.callback_query(F.data == "sched_export")
async def sched_export_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await export_schedule_cmd(cb.message, state)  # шлём документ отдельным сообщением
    await cb.answer()

@router.callback_query(F.data == "sched_import")
async def sched_import_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await safe_edit(
        cb.message,
        "📥 Send a CSV file with header at least: <code>time_str,account_id,account_title,text</code>\n"
        "Optional: <code>dow_mask</code> or <code>days</code> (Daily / Weekdays / Weekends / Mon,Wed,Fri)",
        reply_markup=schedule_menu()
    )
    await state.set_state(ImportCSV.waiting_doc)
    await cb.answer()

@router.callback_query(F.data == "sched_next")
async def sched_next_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await next_runs_cmd(cb.message, state)
    await cb.answer()

@router.message(Command("cancel"), StateFilter("*"))
async def cancel_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("✖️ Canceled.", reply_markup=schedule_menu())
