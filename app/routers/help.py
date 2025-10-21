# app/routers/help.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.services.safe_edit import safe_edit

router = Router()

HELP_TEXT = (
    "<b>Help & Commands</b>\n\n"
    "• <b>📝 Post now</b> — publish immediately (text + up to 10 photos).\n"
    "• <b>⏱ Schedule</b> — set timers for auto-posting.\n"
    "• <b>🔑 Accounts</b> — manage your Threads accounts.\n"
    "• <b>⚙️ Settings</b> — configure notifications and your time zone.\n"
    "\n"
    "<b>Available Commands:</b>\n"
    "/start — show main menu\n"
    "/menu — show main inline menu\n"
    "/help — show this message\n"
    "/recheck_all — revalidate all your tokens\n"
    "/cancel — cancel current action (like adding a token or post)"
)

def help_kb() -> InlineKeyboardMarkup:
    """Клавиатура для экрана помощи с кнопкой назад в настройки."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back to Settings", callback_data="settings_menu")]
    ])

@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    """Обработчик команды /help."""
    from app.routers.nav import main_menu_kb
    await message.answer(HELP_TEXT, reply_markup=main_menu_kb())

@router.callback_query(F.data == "help_show")
async def help_cb(cb: CallbackQuery) -> None:
    """Обработчик для инлайн-кнопки 'Help'."""
    await safe_edit(cb.message, HELP_TEXT, reply_markup=help_kb())
    await cb.answer()
