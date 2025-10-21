# app/routers/nav.py
# ------------------------------------------------------------
# Главное меню и основная навигация.
# ------------------------------------------------------------

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Union,
)

from app.services.safe_edit import safe_edit

router = Router()


# --- Клавиатуры ---

def main_menu_kb() -> ReplyKeyboardMarkup:
    """Основная Reply-клавиатура (кнопки внизу)."""
    rows = [
        [KeyboardButton(text="📝 Post now"), KeyboardButton(text="⏱ Schedule")],
        [KeyboardButton(text="🔑 Accounts"), KeyboardButton(text="⚙️ Settings")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def inline_main_kb() -> InlineKeyboardMarkup:
    """Основное инлайн-меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Post now", callback_data="post_now")],
        [InlineKeyboardButton(text="⏱ Schedule", callback_data="sched_menu")],
        [InlineKeyboardButton(text="🔑 Accounts", callback_data="token_menu")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings_menu")],
    ])

def settings_menu_kb() -> InlineKeyboardMarkup:
    """Инлайн-меню настроек."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Notifications", callback_data="notify_menu")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="help_show")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")],
    ])


# --- Обработчики ---

@router.message(CommandStart())
async def start_cmd(message: Message) -> None:
    """Обработчик команды /start."""
    await message.answer(
        "Hi! This is a bot for posting to Threads.\n"
        "Use the buttons below to navigate.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("menu"))
@router.message(F.text.in_(["Menu", "menu", "Меню", "меню", "🏠 Menu"]))
async def menu_cmd(message: Message) -> None:
    """
    Обработчик команды /menu и текстовых сообщений для вызова меню.
    Показывает основное инлайн-меню.
    """
    await message.answer("Main menu:", reply_markup=inline_main_kb())


@router.callback_query(F.data == "back_main")
async def back_main_cb(cb: CallbackQuery):
    """Возврат в главное инлайн-меню."""
    await safe_edit(cb.message, "Main menu:", reply_markup=inline_main_kb())
    await cb.answer()


@router.message(F.text == "⚙️ Settings")
@router.callback_query(F.data == "settings_menu")
async def settings_menu_cb(event: Union[Message, CallbackQuery]):
    """Отображает меню настроек."""
    text = "Settings:"
    markup = settings_menu_kb()
    if isinstance(event, Message):
        await event.answer(text, reply_markup=markup)
    else:
        await safe_edit(event.message, text, reply_markup=markup)
        await event.answer()
