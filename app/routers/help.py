# app/routers/help.py
from __future__ import annotations

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

# ВАЖНО: main_menu_kb находится в nav.py, а не в keyboards.py
from app.routers.nav import main_menu_kb
from app.services.safe_edit import safe_edit

router = Router()

HELP_TEXT = (
    "Help:\n"
    "• 📝 <b>Post now</b> — publish immediately (text + up to 10 photos)\n"
    "• ⏱ <b>Schedule</b> — set timers for auto-posting; supports multiple times and day-of-week picker\n"
    "• 🔑 <b>Token</b> — manage accounts (set default / rename / delete), set and validate tokens\n"
    "• 🔔 <b>Notifications</b> — configure scheduler notifications\n"
    "\n"
    "<b>Commands</b>\n"
    "/start — open menu\n"
    "/help — this help\n"
    "/commands — show this list\n"
    "/recheck_all — revalidate all tokens\n"
    "/cancel — cancel current action\n"
)

@router.message(Command("help"))
async def help_cmd(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_kb())

@router.message(F.text == "ℹ️ Help")
async def help_from_btn(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_kb())

@router.callback_query(F.data == "help_show")
async def help_cb(cb: CallbackQuery) -> None:
    await safe_edit(cb.message, HELP_TEXT, reply_markup=main_menu_kb())
    await cb.answer()

# Алиас — тот же список внутри Help
@router.message(Command("commands"))
async def commands_cmd(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_kb())

@router.callback_query(F.data == "commands_show")
async def commands_cb(cb: CallbackQuery) -> None:
    await safe_edit(cb.message, HELP_TEXT, reply_markup=main_menu_kb())
    await cb.answer()
