# app/keyboards/tokens_kb.py
# ------------------------------------------------------------
# Клавиатуры раздела токенов / аккаунтов
# ------------------------------------------------------------
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def tokens_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="➕ Set token", callback_data="tok_set")],
        [InlineKeyboardButton(text="🧪 Check token", callback_data="tok_check")],
        [InlineKeyboardButton(text="📂 Accounts", callback_data="accounts_menu")],
        [InlineKeyboardButton(text="♻️ Recheck all", callback_data="tok_recheck_all")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="token_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def accounts_pick_kb(accounts: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """
    accounts: список (account_id, label)
    """
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"tok_acc:{acc_id}")]
        for acc_id, label in accounts
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="token_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
