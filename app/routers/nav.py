# app/routers/nav.py
# ------------------------------------------------------------
# Главное меню. ВАЖНО: никаких обработчиков для "🔑 Token" здесь —
# это ловит app/routers/tokens.py (чтобы не сломать меню аккаунтов).
# ------------------------------------------------------------

from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

router = Router()


def inline_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Post now", callback_data="post_now")],
        [InlineKeyboardButton(text="⏱ Schedule", callback_data="sched_menu")],
        [InlineKeyboardButton(text="🔔 Notifications", callback_data="notify_menu")],
        [InlineKeyboardButton(text="🔑 Token", callback_data="token_menu")],
        [InlineKeyboardButton(text="📜 Commands", callback_data="commands_show")],
    ])



def main_menu_kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📝 Post now"), KeyboardButton(text="⏱ Schedule")],
        [KeyboardButton(text="🔑 Token"), KeyboardButton(text="ℹ️ Help")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Hi! This is a bot for posting to Threads.\n"
        "Use the buttons below or the inline menu to navigate.",
        reply_markup=main_menu_kb(),
    )


@router.message(Command("menu"))
@router.message(F.text.in_(["Menu", "menu", "Меню", "меню", "🏠 Menu"]))
async def menu_show(message: Message) -> None:
    await message.answer("Main menu:", reply_markup=main_menu_kb())


@router.message(F.text == "ℹ️ Help")
async def help_cmd(message: Message) -> None:
    await message.answer(
        "Help:\n"
        "• 📝 Post now — publish immediately (text + up to 10 photos)\n"
        "• ⏱ Schedule — set timers for auto-posting\n"
        "• 🔑 Token — manage accounts and validate tokens\n",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data == "back_main")
async def back_main_cb(cb):
    await cb.message.answer("Main menu:", reply_markup=main_menu_kb())
    await cb.answer()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer("Welcome!", reply_markup=main_menu_kb())
    
@router.message(F.text.in_(["Menu", "menu", "Меню", "меню", "🏠 Menu"]))
async def menu_show(message: Message) -> None:
    await message.answer("Main menu:", reply_markup=main_menu_kb())
    
@router.callback_query(F.data == "back_main")
async def back_main_cb(cb):
    await cb.message.answer("Main menu:", reply_markup=main_menu_kb())
    await cb.answer()

@router.callback_query(F.data == "nav_back")
async def nav_back_cb(cb):
    await cb.message.answer("Main menu:", reply_markup=main_menu_kb())
    await cb.answer()