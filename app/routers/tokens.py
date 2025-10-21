# app/routers/tokens.py
from __future__ import annotations
import logging
from html import escape

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, func

from app.database.models import async_session, Account, BotSettings
from app.keyboards import (
    tokens_menu_kb,
    accounts_pick_kb,   # оставляем импорт (не ломаем совместимость)
    accounts_menu_kb,
    account_actions_kb,
)
from app.services.safe_edit import safe_edit
from app.services.token_health import check_token_for_user, recheck_user_tokens
from app.services.threads_client import ThreadsError, get_profile  # для обработки невалидного токена

router = Router(name="tokens")
log = logging.getLogger(__name__)


class SetTokenFSM(StatesGroup):
    waiting_token = State()


@router.message(F.text == "🔑 Token")
async def token_menu_from_button(message: Message) -> None:
    await message.answer("Token / Accounts:", reply_markup=tokens_menu_kb())


@router.callback_query(F.data == "token_menu")
async def token_menu_cb(cb: CallbackQuery) -> None:
    await safe_edit(cb.message, "Token / Accounts:", reply_markup=tokens_menu_kb())
    await cb.answer()


async def _create_new_account_for_user(user_id: int) -> Account:
    """
    Создаёт НОВЫЙ аккаунт пользователя (не перетирая существующие).
    Первый созданный становится дефолтным.
    ВАЖНО: access_token заполняем пустой строкой (NOT NULL в БД).
    (ОСТАВЛЕНО для совместимости — но в новом потоке set token мы НЕ создаём черновики.)
    """
    async with async_session() as session:
        count = (await session.execute(
            select(func.count(Account.id)).where(Account.tg_user_id == user_id)
        )).scalar() or 0
        title = f"account {count + 1}"

        acc = Account(tg_user_id=user_id, title=title, access_token="")  # <-- не None!
        session.add(acc)
        await session.commit()
        await session.refresh(acc)

        st = await session.get(BotSettings, user_id)
        if not st:
            st = BotSettings(tg_user_id=user_id, default_account_id=acc.id)
            session.add(st)
            await session.commit()

        return acc


# -------- Set token --------

@router.callback_query(F.data == "tok_set")
async def tok_set(cb: CallbackQuery, state: FSMContext) -> None:
    """
    FIX: больше не создаём "черновой" аккаунт заранее.
    Сначала спрашиваем токен, валидируем его, и только потом пишем в БД.
    Так мы не получаем пустых/битых аккаунтов.
    """
    await state.clear()
    await state.set_state(SetTokenFSM.waiting_token)
    await safe_edit(
        cb.message,
        "Send access token (starts with <code>TH</code>)\n\n/cancel to abort",
    )
    await cb.answer()


# ---- CANCEL во время ввода токена (фикс) ----

@router.message(SetTokenFSM.waiting_token, Command("cancel"))
@router.message(SetTokenFSM.waiting_token, F.text.lower() == "/cancel")
async def tok_set_cancel(message: Message, state: FSMContext) -> None:
    """Корректно прерываем FSM и возвращаемся в меню токенов."""
    await state.clear()
    await message.answer("✖️ Canceled.", reply_markup=tokens_menu_kb())


@router.callback_query(F.data.startswith("acc_pick:"))
async def tok_account_chosen(cb: CallbackQuery, state: FSMContext) -> None:
    """Если где-то вызывается выбор аккаунта для ввода токена (оставлено для совместимости)."""
    # В новой логике всё равно ждём токен и создадим новый аккаунт ПОсле валидации.
    await state.set_state(SetTokenFSM.waiting_token)
    await safe_edit(
        cb.message,
        "Send access token (starts with <code>TH</code>)\n\n/cancel to abort",
    )
    await cb.answer()


@router.message(SetTokenFSM.waiting_token)
async def tok_save_token(message: Message, state: FSMContext) -> None:
    """
    Новая логика:
      1) Получаем строку токена.
      2) Валидируем её через Threads (check_token_for_user(..., access_token=...)).
      3) Если валидно — СОЗДАЁМ новый аккаунт с этим токеном и при необходимости выставляем default.
      4) Если не валидно — ничего не создаём.
    """
    token = (message.text or "").strip()
    user_id = message.from_user.id

    # мягкая первичная проверка формата
    if not token or not token.startswith("TH") or len(token) < 10:
        await message.answer("Looks like a wrong token. Try again or /cancel.")
        return

    # --- проверяем токен у Threads ---
    try:
        prof = await get_profile(token)  # вернёт dict с данными профиля либо кинет ThreadsError
    except ThreadsError as e:
        await message.answer("Looks like a wrong token. Try again or /cancel.")
        return
    except Exception as e:
        await message.answer("Temporary error while validating token. Try again a bit later or /cancel.")
        log.exception("token validate error: %s", e)
        return
    title = None
    if isinstance(prof, dict):
        title = prof.get("username") or prof.get("id")
    title = title or "account"

    # --- сохраняем аккаунт только сейчас ---
    async with async_session() as session:
        # Считаем порядковый номер для читаемого title, если username не пришёл
        if title == "account":
            count = (await session.execute(
                select(func.count(Account.id)).where(Account.tg_user_id == user_id)
            )).scalar() or 0
            title = f"account {count + 1}"

        acc = Account(tg_user_id=user_id, title=title, access_token=token)
        session.add(acc)
        await session.flush()  # получить acc.id

        # если дефолтный ещё не выставлен — выставим на этот
        st = await session.get(BotSettings, user_id)
        if not st:
            st = BotSettings(tg_user_id=user_id, default_account_id=acc.id)
            session.add(st)
        elif not st.default_account_id:
            st.default_account_id = acc.id

        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Token saved for <b>{escape(title)}</b>.",
        reply_markup=tokens_menu_kb(),
    )


# -------- Check / Recheck --------

@router.callback_query(F.data == "tok_check")
async def tok_check(entry) -> None:
    msg = entry.message if hasattr(entry, "message") else entry
    user_id = msg.chat.id
    status, info = await check_token_for_user(user_id)
    if status == "ok":
        await msg.answer("✅ Token is valid.", reply_markup=tokens_menu_kb())
    else:
        await msg.answer(f"❌ Token error: {info or 'unknown'}", reply_markup=tokens_menu_kb())
    if hasattr(entry, "answer"):
        await entry.answer()


@router.callback_query(F.data == "tok_recheck_all")
async def tok_recheck_all(entry) -> None:
    msg = entry.message if hasattr(entry, "message") else entry
    user_id = msg.chat.id
    summary = await recheck_user_tokens(user_id)
    lines = [f"Checked: {summary.get('total', 0)} — ok: {summary.get('ok', 0)}, error: {summary.get('error', 0)}"]
    for acc_id, title, status, err in summary.get("details", []):
        line = f"• {title} (id={acc_id}): {status}"
        if err and status != "ok":
            line += f" — {err}"
        lines.append(line)
    await msg.answer("\n".join(lines), reply_markup=tokens_menu_kb())
    if hasattr(entry, "answer"):
        await entry.answer()


# -------- Accounts (просмотр) --------

@router.callback_query(F.data == "accounts_menu")
async def tok_accounts_menu(cb: CallbackQuery) -> None:
    await safe_edit(cb.message, "Accounts:", reply_markup=accounts_menu_kb())
    await cb.answer()


@router.callback_query(F.data == "accounts_list")
async def tok_accounts_list(cb: CallbackQuery) -> None:
    """
    Показываем список аккаунтов с подсветкой ⭐ default.
    (Не меняем имя функции; строим клавиатуру здесь, чтобы видеть звёздочку.)
    """
    user_id = cb.from_user.id
    async with async_session() as session:
        accs = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()
        st = await session.get(BotSettings, user_id)

    if not accs:
        await safe_edit(cb.message, "No accounts yet. Press <b>Set token</b> to add one.",
                        reply_markup=tokens_menu_kb())
        await cb.answer()
        return

    default_id = st.default_account_id if st else None
    rows = []
    for a in accs:
        title = a.title or f"account {a.id}"
        star = "⭐ " if default_id and a.id == default_id else ""
        rows.append([InlineKeyboardButton(text=f"{star}{title}", callback_data=f"acc_setdef:{a.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="token_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await safe_edit(cb.message, "Choose an account:", reply_markup=kb)
    await cb.answer()
