# app/keyboards.py
# ------------------------------------------------------------
# Общие инлайн-клавиатуры:
#   • Token / Accounts:  tokens_menu_kb, accounts_menu_kb, accounts_pick_kb, account_actions_kb
#   • Notifications:     notify_menu
#   • Schedule:          schedule_menu, dow_picker_kb
# ------------------------------------------------------------

from __future__ import annotations

from typing import Iterable, List
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


# =========================
#   TOKEN / ACCOUNTS
# =========================

def tokens_menu_kb() -> InlineKeyboardMarkup:
    """
    Инлайн-меню раздела Token / Accounts.
      • Set token    → tok_set
      • Check token  → tok_check
      • Recheck all  → tok_recheck_all
      • Accounts     → accounts_menu
      • Back         → back_main   ← возвращаем в главное меню (по запросу)
    """
    rows = [
        [InlineKeyboardButton(text="➕ Set token",   callback_data="tok_set")],
        [InlineKeyboardButton(text="🧪 Check token", callback_data="tok_check")],
        [InlineKeyboardButton(text="♻️ Recheck all", callback_data="tok_recheck_all")],
        [InlineKeyboardButton(text="👤 Accounts",    callback_data="accounts_menu")],
        [InlineKeyboardButton(text="⬅️ Back",        callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def accounts_menu_kb() -> InlineKeyboardMarkup:
    """Подменю Accounts внутри раздела Token."""
    rows = [
        [InlineKeyboardButton(text="📃 List",   callback_data="accounts_list")],
        [InlineKeyboardButton(text="✏️ Rename", callback_data="acc_rename_menu")],
        [InlineKeyboardButton(text="🗑 Delete",  callback_data="acc_delete_menu")],
        # назад — в меню раздела токенов
        [InlineKeyboardButton(text="⬅️ Back",   callback_data="token_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def accounts_pick_kb(accounts: Iterable) -> InlineKeyboardMarkup:
    """
    Инлайн-выбор аккаунта.
    Ожидается iterable с объектами, у которых есть .id и .title.
    callback_data: 'acc_pick:<id>'
    """
    rows: List[List[InlineKeyboardButton]] = []
    for a in accounts:
        title = getattr(a, "title", None) or f"id={getattr(a, 'id', '?')}"
        rows.append([
            InlineKeyboardButton(text=title, callback_data=f"acc_pick:{getattr(a, 'id', 0)}")
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="token_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_actions_kb(acc_id: int) -> InlineKeyboardMarkup:
    """Кнопки действий для конкретного аккаунта."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Set default", callback_data=f"acc_setdef:{acc_id}")],
        [InlineKeyboardButton(text="✏️ Rename",     callback_data=f"acc_rename:{acc_id}")],
        [InlineKeyboardButton(text="🗑 Delete",      callback_data=f"acc_delete:{acc_id}")],
        [InlineKeyboardButton(text="⬅️ Back",       callback_data="token_menu")],
    ])


# =========================
#   NOTIFICATIONS
# =========================

def notify_menu() -> InlineKeyboardMarkup:
    """
    Инлайн-меню уведомлений планировщика.
      • Here   → notify_here
      • Status → notify_status
      • Test   → notify_test
      • Off    → notify_off
      • Back   → token_menu
    """
    rows = [
        [InlineKeyboardButton(text="📍 Here",   callback_data="notify_here")],
        [InlineKeyboardButton(text="📊 Status", callback_data="notify_status")],
        [InlineKeyboardButton(text="🧪 Test",   callback_data="notify_test")],
        [InlineKeyboardButton(text="🔕 Off",    callback_data="notify_off")],
        [InlineKeyboardButton(text="⬅️ Back",   callback_data="token_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
#   SCHEDULE
# =========================

def schedule_menu() -> InlineKeyboardMarkup:
    """
    Главное меню раздела Schedule.
      • Add times     → sched_add
      • List timers   → sched_list
      • Remove by time→ sched_remove
      • Clear all     → sched_clear
      • Export CSV    → sched_export
      • Import CSV    → sched_import
      • Next 7 days   → sched_next
      • Back          → back_main
    """
    rows = [
        [InlineKeyboardButton(text="➕ Add times",       callback_data="sched_add")],
        [InlineKeyboardButton(text="📃 List timers",     callback_data="sched_list")],
        [InlineKeyboardButton(text="🕘 Remove by time",  callback_data="sched_remove")],
        [InlineKeyboardButton(text="🧹 Clear all",       callback_data="sched_clear")],
        [InlineKeyboardButton(text="📤 Export CSV",      callback_data="sched_export")],
        [InlineKeyboardButton(text="📥 Import CSV",      callback_data="sched_import")],
        [InlineKeyboardButton(text="📅 Next 7 days",     callback_data="sched_next")],
        [InlineKeyboardButton(text="⬅️ Back",            callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dow_picker_kb(mask: int) -> InlineKeyboardMarkup:
    """
    Пикер дней недели.
      • Тогглы дней: dow_t:0..6
      • Пресеты:     dow_all / dow_wkd / dow_wke
      • ОК / Cancel: dow_ok / dow_cancel
      • Back:        sched_menu
    """
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    rows: List[List[InlineKeyboardButton]] = []
    for i, d in enumerate(days):
        picked = bool(mask & (1 << i))
        label = f"{'• ' if picked else ''}{d}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"dow_t:{i}")])
    rows.append([InlineKeyboardButton(text="Daily",    callback_data="dow_all")])
    rows.append([InlineKeyboardButton(text="Weekdays", callback_data="dow_wkd")])
    rows.append([InlineKeyboardButton(text="Weekends", callback_data="dow_wke")])
    rows.append([InlineKeyboardButton(text="✅ OK",     callback_data="dow_ok")])
    rows.append([InlineKeyboardButton(text="✖️ Cancel", callback_data="dow_cancel")])
    rows.append([InlineKeyboardButton(text="⬅️ Back",   callback_data="sched_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
