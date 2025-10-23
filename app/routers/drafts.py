# app/routers/drafts.py
# ------------------------------------------------------------
# (ИЗМЕНЕНИЕ) Исправлена ошибка RuntimeError: This method is not mounted... (снова)
# Роутер для управления черновиками.
# ------------------------------------------------------------

import logging
from html import escape
from datetime import datetime
from typing import List, Optional, Union # Добавлен Union

from aiogram import Router, F, types
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, delete, update, desc, func # Добавлен func
from sqlalchemy.orm import selectinload


from app.database.models import async_session, Draft, DraftMedia
from app.services.safe_edit import safe_edit
from app.services.ai_assistant import suggest_hashtags
from app.keyboards import (
    drafts_menu_kb, draft_view_kb, draft_manage_media_kb,
    draft_copy_kb, draft_delete_confirm_kb, main_menu_kb, back_button
)

log = logging.getLogger(__name__)
router = Router()

# ---------------------- FSM ---------------------- #

class DraftFSM(StatesGroup):
    waiting_text = State()
    viewing = State() # Общее состояние просмотра/редактирования
    editing_text = State()
    managing_media = State()
    adding_media = State()


# -------------------- СПИСОК ЧЕРНОВИКОВ ----------------------- #

async def drafts_list_menu(evt: Union[CallbackQuery, Message], state: FSMContext):
    """Отображает список черновиков."""
    await state.clear()
    user_id = evt.from_user.id

    # Определяем целевое сообщение
    target_message: Message # Объявляем тип
    is_callback = isinstance(evt, CallbackQuery) # Проверяем тип заранее

    if is_callback:
        target_message = evt.message
        # Отвечаем на колбек только если это *не* возврат после удаления
        # или создания/сохранения черновика
        if not evt.data.startswith("draft_delete_confirm") \
           and not evt.data.startswith("draft_view:") \
           and not evt.data.startswith("draft_save_media:"):
             await evt.answer()
    else: # Если это Message
        target_message = evt

    async with async_session() as session:
        drafts = (await session.execute(
            select(Draft)
            .where(Draft.tg_user_id == user_id)
            .order_by(desc(Draft.id)) # Сортируем по убыванию ID (новые сверху)
            .limit(20) # Ограничим для производительности
            .options(selectinload(Draft.media))
        )).scalars().all()

    text = "📄 **Drafts**\n\nSelect a draft to view/edit or create a new one:"
    if not drafts:
        text = "📄 **Drafts**\n\n_You have no drafts yet._"

    # Используем target_message
    await safe_edit(target_message, text, reply_markup=drafts_menu_kb(drafts))


# -------------------- СОЗДАНИЕ ЧЕРНОВИКА --------------------- #

@router.callback_query(F.data == "draft_create")
async def draft_create_start(cb: CallbackQuery, state: FSMContext):
    """Начинает создание нового черновика, просит текст."""
    await state.set_state(DraftFSM.waiting_text)
    await safe_edit(cb.message, "✏️ Send the text for your new draft.\n\n/cancel to abort.")
    await cb.answer()

@router.message(DraftFSM.waiting_text)
async def draft_create_got_text(message: Message, state: FSMContext):
    """Сохраняет текст и переходит к добавлению медиа."""
    text = message.text
    if not text or text.lower() == '/cancel':
        await state.clear()
        await message.answer("Cancelled creation.", reply_markup=main_menu_kb())
        return

    user_id = message.from_user.id
    async with async_session() as session:
        # Создаем черновик в базе
        new_draft = Draft(tg_user_id=user_id, text=text)
        session.add(new_draft)
        await session.commit()
        await session.refresh(new_draft) # Получаем ID
        draft_id = new_draft.id

    await state.set_state(DraftFSM.adding_media)
    await state.update_data(draft_id=draft_id, media_files=[]) # Храним file_id фото
    # Клавиатура для управления после добавления медиа
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Done Adding Media", callback_data=f"draft_save_media:{draft_id}")],
        [InlineKeyboardButton(text="↩️ Cancel Draft", callback_data=f"draft_delete_confirm:{draft_id}")] # Предлагаем удалить, если передумал
    ])
    await message.answer(
        f"✅ Draft text saved (ID: {draft_id}).\n"
        f"Now send up to 10 photos (as separate messages).\n"
        f"Press 'Done' when finished or to skip adding media.",
        reply_markup=kb
    )

# -------------------- ПРОСМОТР И РЕДАКТИРОВАНИЕ ----------------- #

@router.callback_query(F.data.startswith("draft_view:"))
async def draft_view(cb: CallbackQuery, state: FSMContext):
    """Отображает черновик и кнопки действий."""
    try:
        draft_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        # --- (ИСПРАВЛЕНИЕ) Добавляем await перед cb.answer() ---
        await cb.answer("Invalid draft ID.", show_alert=True); return
        # ---

    await state.clear() # Сбрасываем предыдущее состояние FSM
    user_id = cb.from_user.id

    async with async_session() as session:
        draft = await session.get(Draft, draft_id, options=[selectinload(Draft.media)])
        if not draft or draft.tg_user_id != user_id:
            # --- (ИСПРАВЛЕНИЕ) Добавляем await перед cb.answer() ---
            await cb.answer("Draft not found.", show_alert=True)
            # ---
            # Попробуем вернуться к списку
            fake_evt = types.CallbackQuery(id='fake_notfound', from_user=cb.from_user, message=cb.message, chat_instance='fake_notfound', data="drafts_menu")
            await drafts_list_menu(fake_evt, state)
            return

        media_count = len(draft.media)
        text = draft.text or "(No text)"
        hashtags = draft.suggested_hashtags or ""

    display_text = (
        f"📄 **Draft #{draft_id}**\n\n"
        f"📝 **Text:**\n{escape(text)}\n\n"
        f"🖼️ **Media Attached:** {media_count}/10"
    )
    if hashtags:
        display_text += f"\n\n✨ **Suggested Hashtags:**\n{escape(hashtags)}"

    # Устанавливаем состояние просмотра, чтобы кнопки работали
    await state.set_state(DraftFSM.viewing)
    await state.update_data(current_draft_id=draft_id)

    await safe_edit(cb.message, display_text, reply_markup=draft_view_kb(draft_id))
    # --- (ИСПРАВЛЕНИЕ) Удаляем cb.answer(), так как он не нужен при вызове через fake_cb ---
    # await cb.answer() # <--- ЭТА СТРОКА УДАЛЕНА
    # ---


@router.callback_query(DraftFSM.viewing, F.data.startswith("draft_edit_text:"))
async def draft_edit_text_start(cb: CallbackQuery, state: FSMContext):
    """Запрашивает новый текст для черновика."""
    await state.set_state(DraftFSM.editing_text)
    # draft_id уже должен быть в state.update_data из draft_view
    await safe_edit(cb.message, "✏️ Send the new text for this draft.\n\n/cancel to keep current text.")
    await cb.answer()

@router.message(DraftFSM.editing_text)
async def draft_edit_text_finish(message: Message, state: FSMContext):
    """Сохраняет измененный текст."""
    new_text = message.text
    fsm_data = await state.get_data()
    draft_id = fsm_data.get("current_draft_id")

    if not new_text or new_text.lower() == '/cancel':
        await message.answer("Edit cancelled.")
        # Возвращаемся к просмотру черновика
        if draft_id:
            # --- (ИСПРАВЛЕНИЕ) Используем message из текущего хендлера ---
            fake_cb = types.CallbackQuery(id='fake_cancel_edit', from_user=message.from_user, message=message, chat_instance='fake_cancel_edit', data=f"draft_view:{draft_id}")
            # ---
            await draft_view(fake_cb, state) # Передаем state
        else:
            await state.clear()
        return

    if not draft_id:
        await message.answer("Error: Could not find draft ID. Cancelling edit.")
        await state.clear()
        # Попробуем вернуться к списку
        fake_evt = types.CallbackQuery(id='fake_no_id', from_user=message.from_user, message=message, chat_instance='fake_no_id', data="drafts_menu")
        await drafts_list_menu(fake_evt, state)
        return

    async with async_session() as session:
        await session.execute(
            update(Draft)
            .where(Draft.id == draft_id, Draft.tg_user_id == message.from_user.id)
            .values(text=new_text)
        )
        await session.commit()

    await message.answer("✅ Text updated.")
    # Возвращаемся к просмотру черновика
    # --- (ИСПРАВЛЕНИЕ) Используем message из текущего хендлера ---
    fake_cb = types.CallbackQuery(id='fake_edit_done', from_user=message.from_user, message=message, chat_instance='fake_edit_done', data=f"draft_view:{draft_id}")
    # ---
    await draft_view(fake_cb, state) # Передаем state


# -------------------- УПРАВЛЕНИЕ МЕДИА ---------------------- #

@router.callback_query(DraftFSM.viewing, F.data.startswith("draft_manage_media:"))
async def draft_manage_media_start(cb: CallbackQuery, state: FSMContext):
    """Показывает опции управления медиа."""
    fsm_data = await state.get_data()
    draft_id = fsm_data.get("current_draft_id")
    if not draft_id:
        await cb.answer("Error: Draft context lost.", show_alert=True); return

    user_id = cb.from_user.id
    async with async_session() as session:
        # Получаем только количество медиа
        media_count_result = await session.execute(
            select(func.count(DraftMedia.id)) # Используем func из sqlalchemy
            .where(DraftMedia.draft_id == draft_id)
        )
        media_count = media_count_result.scalar_one_or_none() or 0

    await state.set_state(DraftFSM.managing_media)
    # Сохраняем file_ids в state для возможного удаления/добавления
    # (Хотя проще будет запросить их заново при необходимости)

    text = (
        f"🖼️ **Manage Media for Draft #{draft_id}**\n\n"
        f"Currently attached: {media_count}/10\n\n"
        f"Send photos to add them (up to 10 total).\n"
        f"Use buttons below to clear existing media or go back."
    )
    await safe_edit(cb.message, text, reply_markup=draft_manage_media_kb(draft_id, has_media=media_count > 0))
    await cb.answer()


@router.message(DraftFSM.managing_media, F.photo)
async def draft_manage_media_add(message: Message, state: FSMContext):
    """Добавляет фото в режиме управления медиа."""
    fsm_data = await state.get_data()
    draft_id = fsm_data.get("current_draft_id")
    if not draft_id:
        await message.answer("Error: Draft context lost. Please go back and try again.")
        return

    user_id = message.from_user.id
    largest_photo = max(message.photo, key=lambda p: p.file_size or 0)
    file_id = largest_photo.file_id

    async with async_session() as session:
        # Считаем текущее количество медиа
        media_count_result = await session.execute(
            select(func.count(DraftMedia.id)) # Используем func из sqlalchemy
            .where(DraftMedia.draft_id == draft_id)
        )
        media_count = media_count_result.scalar_one_or_none() or 0

        if media_count >= 10:
            await message.answer("Maximum 10 media files reached. Use 'Clear All Media' first.")
            return

        # Добавляем новое медиа
        new_media = DraftMedia(draft_id=draft_id, tg_file_id=file_id)
        session.add(new_media)
        await session.commit()
        new_media_count = media_count + 1

    # Обновляем сообщение с новым счетчиком
    text = (
        f"🖼️ **Manage Media for Draft #{draft_id}**\n\n"
        f"✅ Added photo ({new_media_count}/10)\n\n"
        f"Send more photos or use buttons below."
    )
    # Используем message.reply_to_message, если хотим отредактировать сообщение бота, а не слать новое
    # Но так как мы не храним message_id бота, проще отправить новое сообщение или обновить предыдущее
    # safe_edit обновит последнее сообщение бота в этом диалоге (если оно было от колбека)
    # Найдем последнее сообщение бота - это сложно. Проще отправить новое.
    await message.answer(text, reply_markup=draft_manage_media_kb(draft_id, has_media=True))


# Этот обработчик нужен для случая, когда пользователь начал создавать черновик
@router.message(DraftFSM.adding_media, F.photo)
async def draft_create_add_photo(message: Message, state: FSMContext):
    """Добавляет фото при создании черновика."""
    fsm_data = await state.get_data()
    draft_id = fsm_data.get("draft_id")
    media_files: list = fsm_data.get("media_files", [])
    if not draft_id:
        await message.answer("Error: Draft creation context lost. Please start over.")
        await state.clear()
        return

    largest_photo = max(message.photo, key=lambda p: p.file_size or 0)
    file_id = largest_photo.file_id

    if len(media_files) >= 10:
        await message.answer("Maximum 10 media files reached.")
        return

    media_files.append(file_id)
    await state.update_data(media_files=media_files)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Done Adding Media", callback_data=f"draft_save_media:{draft_id}")], # Новая кнопка для сохранения
        [InlineKeyboardButton(text="↩️ Cancel Draft", callback_data=f"draft_delete_confirm:{draft_id}")]
    ])
    await message.answer(f"✅ Added photo ({len(media_files)}/10). Press 'Done' when finished.", reply_markup=kb)

@router.callback_query(DraftFSM.adding_media, F.data.startswith("draft_save_media:"))
async def draft_create_save_media(cb: CallbackQuery, state: FSMContext):
    """Сохраняет добавленные медиа в базу и переходит к просмотру."""
    fsm_data = await state.get_data()
    draft_id = fsm_data.get("draft_id")
    media_files: list = fsm_data.get("media_files", [])

    if not draft_id:
        await cb.answer("Error: Draft context lost.", show_alert=True)
        await state.clear()
        return

    async with async_session() as session:
        for file_id in media_files:
            new_media = DraftMedia(draft_id=draft_id, tg_file_id=file_id)
            session.add(new_media)
        await session.commit()

    await cb.answer("Media saved.")
    # Переходим к просмотру созданного черновика
    # Имитируем нажатие кнопки draft_view, передаем draft_id
    fake_cb = types.CallbackQuery(id='fake_save_media', from_user=cb.from_user, message=cb.message, chat_instance='fake_save_media', data=f"draft_view:{draft_id}")
    await draft_view(fake_cb, state) # Передаем state


@router.callback_query(DraftFSM.managing_media, F.data.startswith("draft_clear_media:"))
async def draft_manage_media_clear(cb: CallbackQuery, state: FSMContext):
    """Удаляет все медиа из черновика."""
    fsm_data = await state.get_data()
    draft_id = fsm_data.get("current_draft_id")
    if not draft_id:
        await cb.answer("Error: Draft context lost.", show_alert=True); return

    user_id = cb.from_user.id
    async with async_session() as session:
        # Проверяем, что черновик принадлежит пользователю перед удалением медиа
        draft = await session.get(Draft, draft_id)
        if not draft or draft.tg_user_id != user_id:
            await cb.answer("Draft not found.", show_alert=True)
            return
        # Удаляем связанные медиа
        await session.execute(
            delete(DraftMedia).where(DraftMedia.draft_id == draft_id)
        )
        await session.commit()

    await cb.answer("All media cleared.")
    # Обновляем меню управления медиа (кнопка Clear станет неактивной)
    text = (
        f"🖼️ **Manage Media for Draft #{draft_id}**\n\n"
        f"Currently attached: 0/10\n\n"
        f"Send photos to add them (up to 10 total)."
    )
    await safe_edit(cb.message, text, reply_markup=draft_manage_media_kb(draft_id, has_media=False))

# --- Генерация хэштегов ---
@router.callback_query(DraftFSM.viewing, F.data.startswith("draft_suggest_hashtags:"))
async def draft_suggest_hashtags_handler(cb: CallbackQuery, state: FSMContext): # Переименовано во избежание конфликта
    """Генерирует хэштеги для текста черновика."""
    fsm_data = await state.get_data()
    draft_id = fsm_data.get("current_draft_id")
    if not draft_id:
        await cb.answer("Error: Draft context lost.", show_alert=True); return

    user_id = cb.from_user.id
    async with async_session() as session:
        draft_text = (await session.execute(
            select(Draft.text).where(Draft.id == draft_id, Draft.tg_user_id == user_id)
        )).scalar_one_or_none()

    if not draft_text:
        await cb.answer("Draft text is empty, cannot suggest hashtags.", show_alert=True)
        return

    await cb.answer("✨ Generating hashtags with AI...")
    suggested_tags_text = await suggest_hashtags(draft_text) # Используем импортированную функцию

    # Сохраняем теги в базу
    async with async_session() as session:
         await session.execute(
             update(Draft)
             .where(Draft.id == draft_id)
             .values(suggested_hashtags=suggested_tags_text)
         )
         await session.commit()

    # Обновляем сообщение с просмотром черновика, добавляя теги
    # Для этого нужно снова запросить данные черновика
    async with async_session() as session:
        # Используем selectinload для загрузки медиа одним запросом
        draft = await session.get(Draft, draft_id, options=[selectinload(Draft.media)])
        if not draft: # Маловероятно, но на всякий случай
             await cb.answer("Error retrieving draft after saving hashtags.", show_alert=True)
             return
        media_count = len(draft.media)
        text = draft.text or "(No text)"
        hashtags = draft.suggested_hashtags or "" # Теперь они должны быть

    display_text = (
        f"📄 **Draft #{draft_id}**\n\n"
        f"📝 **Text:**\n{escape(text)}\n\n"
        f"🖼️ **Media Attached:** {media_count}/10"
    )
    if hashtags:
        display_text += f"\n\n✨ **Suggested Hashtags:**\n{escape(hashtags)}"

    await safe_edit(cb.message, display_text, reply_markup=draft_view_kb(draft_id))
    # await cb.answer() # Ответ уже был ("Generating...")


# --- Копирование и Удаление ---
@router.callback_query(DraftFSM.viewing, F.data.startswith("draft_copy:"))
async def draft_copy_for_threads(cb: CallbackQuery, state: FSMContext):
    """Отправляет текст и хэштеги в формате для копирования."""
    fsm_data = await state.get_data()
    draft_id = fsm_data.get("current_draft_id")
    if not draft_id:
        await cb.answer("Error: Draft context lost.", show_alert=True); return

    user_id = cb.from_user.id
    async with async_session() as session:
        draft = await session.get(Draft, draft_id)
        if not draft or draft.tg_user_id != user_id:
            await cb.answer("Draft not found.", show_alert=True); return
        text = draft.text or ""
        hashtags = draft.suggested_hashtags or ""

    copy_text = text
    if hashtags:
        copy_text += "\n\n" + hashtags # Добавляем хештеги через пустую строку

    # Отправляем отдельным сообщением, чтобы его было легко скопировать
    if copy_text:
        # Используем HTML для блока кода <pre><code>...</code></pre>
        await cb.message.answer(
            f"👇 Copy the text below and paste it into Threads:\n\n"
            f"<pre><code>{escape(copy_text)}</code></pre>\n",
            parse_mode="HTML",
            reply_markup=draft_copy_kb(draft_id) # Кнопка Назад
        )
        await cb.answer("Text prepared for copying.")
    else:
        await cb.answer("Draft is empty.", show_alert=True)


@router.callback_query(F.data.startswith("draft_delete:"))
async def draft_delete_confirm_start(cb: CallbackQuery, state: FSMContext):
    """Запрашивает подтверждение удаления черновика."""
    try:
        draft_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid draft ID.", show_alert=True); return

    # Сохраняем ID для подтверждения, хотя можно передавать и в кнопке
    await state.update_data(delete_draft_id=draft_id)

    await safe_edit(cb.message, f"🗑️ Are you sure you want to delete Draft #{draft_id}?",
                    reply_markup=draft_delete_confirm_kb(draft_id))
    await cb.answer()


@router.callback_query(F.data.startswith("draft_delete_confirm:"))
async def draft_delete_confirm_finish(cb: CallbackQuery, state: FSMContext):
    """Окончательно удаляет черновик."""
    try:
        draft_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid draft ID.", show_alert=True); return

    user_id = cb.from_user.id
    async with async_session() as session:
        # Удаляем сам черновик (связанные медиа удалятся каскадно благодаря настройке в models.py)
        result = await session.execute(
            delete(Draft).where(Draft.id == draft_id, Draft.tg_user_id == user_id)
        )
        await session.commit()

    if result.rowcount > 0:
        await cb.answer("Draft deleted.")
    else:
        await cb.answer("Draft not found or already deleted.", show_alert=True)
        # Возвращаемся к списку черновиков в любом случае
        await state.clear()
        fake_evt = types.CallbackQuery(id='fake_delete_done', from_user=cb.from_user, message=cb.message, chat_instance='fake_delete_done', data="drafts_menu")
        await drafts_list_menu(fake_evt, state)
        return # Выходим, чтобы не пытаться отправить еще одно сообщение ниже

    # Возвращаемся к списку черновиков, отправляя НОВОЕ сообщение
    await state.clear()
    user_id = cb.from_user.id
    async with async_session() as session:
        drafts = (await session.execute(
            select(Draft)
            .where(Draft.tg_user_id == user_id)
            .order_by(desc(Draft.id))
            .limit(20)
            .options(selectinload(Draft.media))
        )).scalars().all()
    text = "📄 **Drafts**\n\nSelect a draft to view/edit or create a new one:"
    if not drafts:
        text = "📄 **Drafts**\n\n_You have no drafts yet._"

    # Отправляем новое сообщение вместо редактирования старого
    try:
         await cb.message.answer(text, reply_markup=drafts_menu_kb(drafts))
         # Попытаемся удалить сообщение с подтверждением, если получится
         await cb.message.delete()
    except Exception as e:
         log.warning("Could not delete confirmation message or send new list after draft deletion: %s", e)


# Возврат из управления медиа к просмотру черновика
@router.callback_query(DraftFSM.managing_media, F.data.startswith("draft_view:"))
async def back_from_manage_media_to_view(cb: CallbackQuery, state: FSMContext):
     await draft_view(cb, state)

# Возврат из копирования к просмотру черновика
@router.callback_query(F.data.startswith("draft_view:"), ~StateFilter(DraftFSM.viewing))
async def back_to_draft_view_generic(cb: CallbackQuery, state: FSMContext):
    """Обрабатывает кнопку 'Back to Draft' из других мест (например, Copy)."""
    await draft_view(cb, state)

# Кнопка 'Back to Drafts List' из просмотра черновика
@router.callback_query(DraftFSM.viewing, F.data == "drafts_menu")
async def back_to_drafts_list(cb: CallbackQuery, state: FSMContext):
    """Возвращает к списку черновиков из режима просмотра."""
    await drafts_list_menu(cb, state)

