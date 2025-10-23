# app/routers/nav.py
# ------------------------------------------------------------
# (ИЗМЕНЕНИЕ) Исправлена ошибка ValidationError для ReplyKeyboardMarkup.
# (ИЗМЕНЕНИЕ) Исправлено использование safe_edit.
# Роутер для базовой навигации: /start, /menu, Settings.
# ------------------------------------------------------------

from typing import Union # <-- Импортировано
import logging

from aiogram import Router, F, types
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton # Добавлен KeyboardButton
from aiogram.fsm.context import FSMContext

from app.keyboards import main_menu_kb, settings_menu_kb # Инлайн-клавиатуры
# Импорты для обработки кнопок Drafts и Archive
from .drafts import drafts_list_menu
from .archive import archive_list_dates
# (ИСПРАВЛЕНИЕ) Импортируем safe_edit напрямую
from app.services.safe_edit import safe_edit


log = logging.getLogger(__name__)
router = Router()

# (ИЗМЕНЕНО) Лог для проверки загрузки роутера
log.info("Navigation router loaded!")

# --- Нижняя (Reply) клавиатура ---
# Определяем макет кнопок
main_reply_kb_layout = [
    ["📝 Post now", "⏱ Schedule"],
    ["📄 Drafts", "🔑 Accounts"],
    ["⚙️ Settings"]
]


# --- Обработчики команд ---
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start."""
    await state.clear()
    # (ИСПРАВЛЕНИЕ) Создаем кнопки здесь
    keyboard_buttons = [
        [KeyboardButton(text=text) for text in row]
        for row in main_reply_kb_layout
    ]
    kb = types.ReplyKeyboardMarkup(keyboard=keyboard_buttons, resize_keyboard=True)
    await message.answer(
        "Welcome! 👋\nI can help you schedule posts or post directly to Threads.",
        reply_markup=kb,
    )
    # Сразу покажем инлайн-меню тоже
    await message.answer("Choose an action:", reply_markup=main_menu_kb())

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    """Обработчик команды /menu."""
    await state.clear()
    await message.answer("Main menu:", reply_markup=main_menu_kb())

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """Обработчик команды /cancel (глобальный)."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("No active action to cancel.", reply_markup=main_menu_kb())
        return

    log.info("Cancelling state %r for user %d", current_state, message.from_user.id)
    await state.clear()
    await message.answer("Action cancelled.", reply_markup=main_menu_kb())

# --- Обработчики CallbackQuery ---
@router.callback_query(F.data == "back_main")
async def back_to_main_menu(cb: CallbackQuery, state: FSMContext):
    """Возврат в главное меню из других разделов."""
    await state.clear()
    # (ИСПРАВЛЕНИЕ) Используем safe_edit напрямую
    await safe_edit(cb.message, "Main menu:", reply_markup=main_menu_kb())
    await cb.answer()

@router.callback_query(F.data == "settings_menu")
async def show_settings_menu(cb: CallbackQuery, state: FSMContext):
    """Показывает меню настроек."""
    await state.clear() # Сбрасываем состояние при входе в настройки
    # (ИСПРАВЛЕНИЕ) Используем safe_edit напрямую
    await safe_edit(cb.message, "Settings:", reply_markup=settings_menu_kb())
    await cb.answer()

# --- Обработчики текстовых кнопок ReplyKeyboard (если нужны) ---
@router.message(F.text == "⚙️ Settings")
async def handle_settings_button(message: Message, state: FSMContext):
    """Обрабатывает нажатие текстовой кнопки 'Settings'."""
    await state.clear()
    await message.answer("Settings:", reply_markup=settings_menu_kb()) # Отправляем инлайн-меню

# --- (НОВОЕ) Обработчики для Drafts и Archive ---
@router.message(F.text == "📄 Drafts")
@router.callback_query(F.data == "drafts_menu")
async def handle_drafts_button(evt: Union[CallbackQuery, Message], state: FSMContext):
    """Обрабатывает кнопку Drafts (текстовую и инлайн)."""
    await drafts_list_menu(evt, state) # Вызываем функцию из drafts.py

@router.message(F.text == "🗄️ Archive") # При нажатии на reply кнопку
@router.callback_query(F.data == "archive_list:0") # При нажатии на inline кнопку в settings
async def handle_archive_button(evt: Union[CallbackQuery, Message], state: FSMContext):
    """Обрабатывает кнопку Archive (текстовую и инлайн)."""
    # Создаем фейковый CallbackQuery, если пришло сообщение
    if isinstance(evt, Message):
        async def mock_answer(*args, **kwargs): pass
        # (ИСПРАВЛЕНИЕ) Используем правильный класс CallbackQuery
        cb = types.CallbackQuery(id='fake_archive', from_user=evt.from_user, message=evt, chat_instance='fake_archive', data="archive_list:0")
        # Добавляем мок-метод answer после создания объекта
        cb.answer = mock_answer
    else:
        cb = evt

    await archive_list_dates(cb, state) # Вызываем функцию из archive.py

