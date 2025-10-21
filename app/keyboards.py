# app/keyboards.py
# ------------------------------------------------------------
# Все инлайн-клавиатуры бота.
# ------------------------------------------------------------

from __future__ import annotations

from typing import Iterable, List
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta

from app.database.models import Job, Account


# =========================
#   ГЛАВНОЕ МЕНЮ И НАСТРОЙКИ
# =========================

def main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню (инлайн-версия)."""
    rows = [
        [InlineKeyboardButton(text="📝 Post now", callback_data="post_now")],
        [InlineKeyboardButton(text="⏱ Schedule", callback_data="sched_menu")],
        [InlineKeyboardButton(text="🔑 Accounts", callback_data="tok_accounts")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu_kb() -> InlineKeyboardMarkup:
    """Меню настроек."""
    rows = [
        [InlineKeyboardButton(text="🔔 Notifications", callback_data="notify_menu")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="help_show")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
#   АККАУНТЫ И ТОКЕНЫ
# =========================

def accounts_menu_kb(accounts: list[Account]) -> InlineKeyboardMarkup:
    """Меню управления аккаунтами. Показывает список аккаунтов или только кнопку "Set"."""
    rows = []
    for acc in accounts:
        default_marker = " ⭐️" if acc.is_default else ""
        label = acc.title or getattr(acc, 'threads_username', f"Account {acc.id}")
        rows.append([
            InlineKeyboardButton(text=f"{label}{default_marker}", callback_data=f"acc_view:{acc.id}")
        ])
    
    rows.append([InlineKeyboardButton(text="🔑 Set token", callback_data="tok_set")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_actions_kb(acc_id: int) -> InlineKeyboardMarkup:
    """Кнопки действий для конкретного аккаунта."""
    rows = [
        [
            InlineKeyboardButton(text="✏️ Rename", callback_data=f"acc_rename:{acc_id}"),
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"acc_delete:{acc_id}"),
        ],
        [InlineKeyboardButton(text="⭐️ Set as default", callback_data=f"acc_set_default:{acc_id}")],
        [InlineKeyboardButton(text="⬅️ Back to Accounts", callback_data="tok_accounts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_delete_confirm_kb(acc_id: int) -> InlineKeyboardMarkup:
    """(ДОБАВЛЕНО) Клавиатура подтверждения удаления аккаунта."""
    rows = [
        [InlineKeyboardButton(text="✅ Yes, delete", callback_data=f"acc_delete_confirm:{acc_id}")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data=f"acc_view:{acc_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
#   УВЕДОМЛЕНИЯ И ЧАСОВОЙ ПОЯС
# =========================

def notify_menu() -> InlineKeyboardMarkup:
    """Меню уведомлений."""
    rows = [
        [InlineKeyboardButton(text="📍 Send reports here", callback_data="notify_here")],
        [InlineKeyboardButton(text="📊 Status", callback_data="notify_status")],
        [InlineKeyboardButton(text="🌍 Time Zone", callback_data="tz_menu")],
        [InlineKeyboardButton(text="🧪 Test", callback_data="notify_test")],
        [InlineKeyboardButton(text="🔕 Off", callback_data="notify_off")],
        [InlineKeyboardButton(text="⬅️ Back to Settings", callback_data="settings_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tz_menu(current_tz: str) -> InlineKeyboardMarkup:
    """Меню настройки часового пояса."""
    rows = [
        [InlineKeyboardButton(text=f"Current: {current_tz}", callback_data="tz_enter")],
        [
            InlineKeyboardButton(text="Europe/Kyiv", callback_data="tz_set:Europe/Kyiv"),
            InlineKeyboardButton(text="Europe/Berlin", callback_data="tz_set:Europe/Berlin"),
        ],
        [InlineKeyboardButton(text="Enter manually...", callback_data="tz_enter")],
        [InlineKeyboardButton(text="⬅️ Back to Notifications", callback_data="notify_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
#         SCHEDULE
# =========================

def schedule_menu() -> InlineKeyboardMarkup:
    """Главное меню раздела Schedule."""
    rows = [
        [
            InlineKeyboardButton(text="➕ Add", callback_data="sched_add"),
            InlineKeyboardButton(text="📃 List", callback_data="sched_list"),
        ],
        [
            InlineKeyboardButton(text="🗓️ Weekly View", callback_data="sched_weekly_view"),
            InlineKeyboardButton(text="🧹 Clear All", callback_data="sched_clear"),
        ],
        [
            InlineKeyboardButton(text="📤 Export", callback_data="sched_export"),
            InlineKeyboardButton(text="📥 Import", callback_data="sched_import"),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dow_picker_kb(mask: int) -> InlineKeyboardMarkup:
    """Компактный пикер дней недели."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    
    # Кнопки дней недели в два ряда
    day_buttons = []
    for i, d in enumerate(days):
        label = f"{'✅ ' if (mask >> i) & 1 else ''}{d}"
        day_buttons.append(InlineKeyboardButton(text=label, callback_data=f"dow_t:{i}"))
    
    rows = [day_buttons[:4], day_buttons[4:]] 
    
    # Кнопки пресетов
    rows.append([
        InlineKeyboardButton(text="Daily", callback_data="dow_all"),
        InlineKeyboardButton(text="Weekdays", callback_data="dow_wkd"),
        InlineKeyboardButton(text="Weekends", callback_data="dow_wke"),
    ])
    
    # Кнопки управления
    rows.append([
        InlineKeyboardButton(text="✅ OK", callback_data="dow_ok"),
        InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu"),
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def weekly_view_kb(days_info: list[tuple[str, str, int]]) -> InlineKeyboardMarkup:
    """Клавиатура для недельного просмотра."""
    rows = []
    for label, date_str, post_count in days_info:
        text = f"{label} ({post_count} posts)" if post_count > 0 else label
        rows.append([InlineKeyboardButton(text=text, callback_data=f"sched_day:{date_str}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back to Schedule", callback_data="sched_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_view_kb(date_str: str) -> InlineKeyboardMarkup:
    """Кнопка "назад" из дневного просмотра."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back to Weekly View", callback_data="sched_weekly_view")]
    ])


def job_list_kb(jobs: list[Job]) -> InlineKeyboardMarkup:
    """Клавиатура со списком задач для выбора."""
    rows = []
    for job in jobs:
        short_text = (job.text or "")[:30]
        if len(job.text or "") > 30: short_text += "..."
        label = f"{job.time_str} - {short_text}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"sched_job_view:{job.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back to Schedule", callback_data="sched_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def job_actions_kb(job_id: int) -> InlineKeyboardMarkup:
    """Кнопки действий для конкретной задачи."""
    rows = [
        [
            InlineKeyboardButton(text="✏️ Edit Text", callback_data=f"sched_job_edit_text:{job_id}"),
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"sched_job_delete:{job_id}")
        ],
        [InlineKeyboardButton(text="⬅️ Back to List", callback_data="sched_list")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def job_delete_confirm_kb(job_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления задачи."""
    rows = [
        [InlineKeyboardButton(text="✅ Yes, delete", callback_data=f"sched_job_delete_confirm:{job_id}")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data=f"sched_job_view:{job_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

