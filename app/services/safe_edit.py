# app/services/safe_edit.py
from __future__ import annotations
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, ReplyKeyboardMarkup

async def safe_edit(message: Message, text: str, reply_markup=None) -> None:
    """
    Безопасная замена edit_text:
    - молча игнорирует "message is not modified"
    - корректно обрабатывает ReplyKeyboardMarkup (ее нельзя передать в edit_text)
    - если нельзя редактировать — удаляет и шлёт новое
    """
    # Если пришёл ReplyKeyboardMarkup — не пытаемся редактировать, а просто заменим сообщением
    if isinstance(reply_markup, ReplyKeyboardMarkup):
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup)
        return

    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        # кейс: тот же текст/markup — не считаем за ошибку
        if "message is not modified" in str(e):
            return
        # кейс: не редактируется (старое сообщение, другое состояние и т.д.)
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer(text, reply_markup=reply_markup)
