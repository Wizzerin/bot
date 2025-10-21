# app/routers/post_now.py
# ------------------------------------------------------------
# 📝 Post now
# Поток:
#   1) /start → кнопка "📝 Post now" или callback "post_now"
#   2) Просим текст → "Send the post text (or /cancel)"
#   3) Разрешаем докинуть до 10 фото (обычные photo-сообщения)
#      показываем превью-счётчик и кнопки:
#        • ✅ Publish    — публикует
#        • ♻️ Clear      — очищает выбранные фото
#        • ↩️ Cancel     — отмена
#   4) Публикуем через publish_auto(text, access_token, image_urls)
#      image_urls строим через get_file_public_url(file_id)
# Особенности:
#   • Если фото не приложены/не удалось собрать URL — уйдёт чисто текст.
#   • Если аккаунтов >1, пытаемся взять BotSettings.default_account_id,
#     иначе — просим выбрать из списка.
# ------------------------------------------------------------

from __future__ import annotations

from typing import List, Optional
import logging
from html import escape

from aiogram import Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy import select

from app.database.models import async_session, Account, BotSettings
from app.services.safe_edit import safe_edit
from app.services.tg_io import get_file_public_url
from app.services.threads_client import publish_auto, ThreadsError

log = logging.getLogger(__name__)
router = Router()


# ---------------------- FSM ---------------------- #

class PostNowFSM(StatesGroup):
    choosing_account = State()   # если у пользователя >1 аккаунта
    waiting_text     = State()   # ждём текст поста
    waiting_media    = State()   # собираем фото и ждём Publish


# -------------------- ВХОД ----------------------- #

@router.message(F.text == "📝 Post now")
@router.callback_query(F.data == "post_now")
async def post_now_start(evt, state: FSMContext):
    """
    Точка входа: если один аккаунт — сразу просим текст,
    если несколько — даём выбор.
    """
    if isinstance(evt, Message):
        user_id = evt.from_user.id
        target_msg = evt
        answer = evt.answer
    else:
        user_id = evt.from_user.id
        target_msg = evt.message
        answer = evt.answer

    async with async_session() as session:
        accounts = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()

        if not accounts:
            await safe_edit(
                target_msg,
                "You have no accounts yet.\nAdd a token first: <code>Token → 🔑 Set token</code> or <code>/set_token TH...</code>",
            )
            if isinstance(evt, CallbackQuery):
                await answer()
            return

        # если только один — используем его
        if len(accounts) == 1:
            await state.update_data(account_id=accounts[0].id, images=[])
            await state.set_state(PostNowFSM.waiting_text)
            await safe_edit(target_msg, "Send the post text (or /cancel).")
            if isinstance(evt, CallbackQuery):
                await answer()
            return

        # если несколько — смотрим дефолт в настройках
        st = await session.get(BotSettings, user_id)
        if st and st.default_account_id:
            await state.update_data(account_id=st.default_account_id, images=[])
            await state.set_state(PostNowFSM.waiting_text)
            await safe_edit(target_msg, "Send the post text (or /cancel).")
            if isinstance(evt, CallbackQuery):
                await answer()
            return

        # иначе — просим выбрать аккаунт
        await state.set_state(PostNowFSM.choosing_account)
        rows = [
            [InlineKeyboardButton(text=f"{a.title or 'untitled'} (id={a.id})", callback_data=f"post_acc:{a.id}")]
            for a in accounts
        ]
        rows.append([InlineKeyboardButton(text="↩️ Cancel", callback_data="post_cancel")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await safe_edit(target_msg, "Choose an account for this post:", reply_markup=kb)
        if isinstance(evt, CallbackQuery):
            await answer()


@router.callback_query(PostNowFSM.choosing_account, F.data.startswith("post_acc:"))
async def post_now_account_picked(cb: CallbackQuery, state: FSMContext):
    try:
        acc_id = int(cb.data.split(":", 1)[1])
    except Exception:
        await cb.answer("Invalid account.", show_alert=True)
        return

    await state.update_data(account_id=acc_id, images=[])
    await state.set_state(PostNowFSM.waiting_text)
    await safe_edit(cb.message, "Send the post text (or /cancel).")
    await cb.answer()


@router.callback_query(PostNowFSM.choosing_account, F.data == "post_cancel")
async def post_now_cancel_from_choose(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "Cancelled.")
    await cb.answer()


# ---------------- СБОР ТЕКСТА ------------------- #

@router.message(PostNowFSM.waiting_text, F.text.as_("text"))
async def post_now_got_text(message: Message, state: FSMContext, text: str):
    text = (text or "").strip()
    if not text or text.lower() == "/cancel":
        await state.clear()
        await message.answer("Cancelled.")
        return

    await state.update_data(text=text, images=[])  # очищаем список фоток на всякий случай

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Publish", callback_data="post_publish"),
            InlineKeyboardButton(text="♻️ Clear images", callback_data="post_clear"),
        ],
        [InlineKeyboardButton(text="↩️ Cancel", callback_data="post_cancel")],
    ])
    await message.answer(
        "Now send up to 10 photos (as separate messages),\n"
        "or press <b>Publish</b> to post without images.",
        reply_markup=kb
    )
    await state.set_state(PostNowFSM.waiting_media)


# -------------- СБОР ФОТО ---------------------- #

@router.message(PostNowFSM.waiting_media, F.photo)
async def post_now_collect_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    images: List[str] = data.get("images", [])

    # берём самый большой вариант
    largest = max(message.photo, key=lambda p: p.file_size or 0)
    file_id = largest.file_id

    if len(images) >= 10:
        await message.answer("You already attached 10 photos (max). Use <b>Clear images</b> to reset.")
        return

    images.append(file_id)
    await state.update_data(images=images)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Publish", callback_data="post_publish"),
            InlineKeyboardButton(text="♻️ Clear images", callback_data="post_clear"),
        ],
        [InlineKeyboardButton(text="↩️ Cancel", callback_data="post_cancel")],
    ])
    await message.answer(f"✅ Added photo ({len(images)}/10).", reply_markup=kb)


# -------------- КНОПКИ: Publish / Clear / Cancel ----------- #

@router.callback_query(PostNowFSM.waiting_media, F.data == "post_clear")
async def post_now_clear(cb: CallbackQuery, state: FSMContext):
    await state.update_data(images=[])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Publish", callback_data="post_publish"),
            InlineKeyboardButton(text="♻️ Clear images", callback_data="post_clear"),
        ],
        [InlineKeyboardButton(text="↩️ Cancel", callback_data="post_cancel")],
    ])
    await safe_edit(cb.message, "Images cleared. You can send new ones, or press Publish.", reply_markup=kb)
    await cb.answer()


@router.callback_query(PostNowFSM.waiting_media, F.data == "post_cancel")
async def post_now_cancel(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(cb.message, "Cancelled.")
    await cb.answer()


@router.callback_query(PostNowFSM.waiting_media, F.data == "post_publish")
async def post_now_publish(cb: CallbackQuery, state: FSMContext):
    user_id = cb.from_user.id
    payload = await state.get_data()
    text: str = payload.get("text", "") or ""
    account_id: Optional[int] = payload.get("account_id")
    file_ids: List[str] = payload.get("images", []) or []

    # 1) Проверим аккаунт
    async with async_session() as session:
        if not account_id:
            # fallback: взять дефолтный или первый
            st = await session.get(BotSettings, user_id)
            if st and st.default_account_id:
                account_id = st.default_account_id
            else:
                first = (await session.execute(
                    select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
                )).scalars().first()
                account_id = first.id if first else None

        if not account_id:
            await safe_edit(cb.message, "No account selected. Add a token first.")
            await cb.answer()
            await state.clear()
            return

        acc = await session.get(Account, account_id)
        if not acc or not acc.access_token:
            await safe_edit(cb.message, "Account has no token. Set token and try again.")
            await cb.answer()
            await state.clear()
            return

    # 2) Преобразуем file_id → публичные URL (могут не получиться — тогда публикуем текст)
    image_urls: List[str] = []
    for fid in file_ids:
        try:
            url = await get_file_public_url(fid)
            if url:
                image_urls.append(url)
        except Exception as e:
            log.warning("post_now: failed to build public url for %s: %s", fid, e)

    # 3) Публикация — только через publish_auto (никогда не уходит кривой media_type)
    try:
        post_id = await publish_auto(text=text, access_token=acc.access_token, image_urls=image_urls)
        await safe_edit(
            cb.message,
            "✅ Published\n"
            f"🧾 {escape(text[:100])}{'…' if len(text) > 100 else ''}\n"
            f"🖼️ images: {len(image_urls)}\n"
            f"🆔 {escape(str(post_id))}"
        )
        await state.clear()
    except ThreadsError as e:
        await safe_edit(cb.message, f"❌ Publish error: {escape(str(e))}")
    except Exception as e:
        log.exception("post_now: unexpected error: %s", e)
        await safe_edit(cb.message, f"❌ Unexpected error: {escape(str(e))}")
    finally:
        await cb.answer()
