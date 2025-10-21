# app/routers/accounts.py
# ------------------------------------------------------------
# Управление Threads-аккаунтами:
#  • Список аккаунтов (⭐ пометка дефолтного) — сразу обновляется
#  • Переименование (Rename)
#  • Удаление (Delete) — с подтверждением
# ------------------------------------------------------------

from __future__ import annotations
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from sqlalchemy import select, delete

from app.database.models import async_session, Account, BotSettings
from app.keyboards import accounts_menu_kb  # оставляем импорты как есть
from app.services.safe_edit import safe_edit

router = Router(name="accounts")


# ---------- helpers ----------

async def _render_accounts_list(msg_obj, user_id: int) -> None:
    """
    Список аккаунтов с ⭐ у дефолтного.
    Всегда строим клавиатуру здесь (без фабрики), чтобы звезда обновлялась мгновенно.
    """
    async with async_session() as session:
        accs = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()
        st = await session.get(BotSettings, user_id)

    if not accs:
        await safe_edit(
            msg_obj,
            "No accounts yet. Press <b>Set token</b> to add one.",
            reply_markup=accounts_menu_kb(),
        )
        return

    default_id = st.default_account_id if st else None
    rows = []
    for a in accs:
        title = a.title or f"account {a.id}"
        star = "⭐ " if default_id and a.id == default_id else ""
        # клик по аккаунту — делает его дефолтным
        rows.append([InlineKeyboardButton(text=f"{star}{title}", callback_data=f"acc_setdef:{a.id}")])
    # назад — в меню раздела токенов
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="token_menu")])

    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await safe_edit(msg_obj, "Choose an account:", reply_markup=kb)


# ---------- вход в подменю ----------

@router.callback_query(F.data == "accounts_menu")
async def accounts_menu(callback: CallbackQuery) -> None:
    await safe_edit(callback.message, "Accounts:", reply_markup=accounts_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "accounts_list")
async def accounts_list(callback: CallbackQuery) -> None:
    await _render_accounts_list(callback.message, callback.from_user.id)
    await callback.answer()


# ---------- Set default (⭐) ----------

@router.callback_query(F.data.startswith("acc_setdef:"))
async def account_pick_as_default(callback: CallbackQuery) -> None:
    """
    Сделать аккаунт дефолтным и МГНОВЕННО перерисовать список со ⭐.
    """
    user_id = callback.from_user.id
    try:
        acc_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Bad account id", show_alert=True)
        return

    async with async_session() as session:
        st = await session.get(BotSettings, user_id)
        if not st:
            st = BotSettings(tg_user_id=user_id, default_account_id=acc_id)
            session.add(st)
        else:
            st.default_account_id = acc_id
        await session.commit()

    await callback.answer("Default account set.")
    await _render_accounts_list(callback.message, user_id)  # ⭐ обновится сразу


# ---------- Rename ----------

class RenameFSM(StatesGroup):
    waiting_title = State()


@router.callback_query(F.data == "acc_rename_menu")
async def acc_rename_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Показываем список для переименования: клик → acc_rename:<id>."""
    user_id = callback.from_user.id
    async with async_session() as session:
        accs = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()

    if not accs:
        await safe_edit(callback.message, "No accounts to rename.", reply_markup=accounts_menu_kb())
        await callback.answer()
        return

    rows = []
    for a in accs:
        title = a.title or f"account {a.id}"
        rows.append([InlineKeyboardButton(text=title, callback_data=f"acc_rename:{a.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="accounts_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await safe_edit(callback.message, "Pick an account to rename:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_rename:"))
async def acc_rename_pick(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        acc_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Bad account id", show_alert=True)
        return
    await state.update_data(rename_id=acc_id)
    await state.set_state(RenameFSM.waiting_title)
    await safe_edit(callback.message, "Send new title for the account:\n\n/cancel to abort")
    await callback.answer()


@router.message(RenameFSM.waiting_title)
async def acc_rename_apply(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    data = await state.get_data()
    acc_id = data.get("rename_id")

    if not title:
        await message.answer("Empty title. Try again or /cancel.")
        return

    async with async_session() as session:
        acc = await session.get(Account, acc_id)
        if not acc:
            await message.answer("Account not found.")
            await state.clear()
            return
        acc.title = title
        await session.commit()

    await state.clear()
    await message.answer("✅ Title updated.", reply_markup=accounts_menu_kb())


# ---------- Delete (с подтверждением) ----------

@router.callback_query(F.data == "acc_delete_menu")
async def acc_delete_menu(callback: CallbackQuery) -> None:
    """Показываем список для удаления: клик → acc_delete:<id> (попросим подтверждение)."""
    user_id = callback.from_user.id
    async with async_session() as session:
        accs = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()

    if not accs:
        await safe_edit(callback.message, "No accounts to delete.", reply_markup=accounts_menu_kb())
        await callback.answer()
        return

    rows = []
    for a in accs:
        title = a.title or f"account {a.id}"
        rows.append([InlineKeyboardButton(text=f"🗑 {title}", callback_data=f"acc_delete:{a.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="accounts_menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await safe_edit(callback.message, "Pick an account to delete:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_delete:"))
async def acc_delete_confirm(callback: CallbackQuery) -> None:
    """
    Первый клик по 'acc_delete:<id>' — спрашиваем подтверждение.
    """
    try:
        acc_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Bad account id", show_alert=True)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, delete", callback_data=f"acc_delete_yes:{acc_id}"),
        ],
        [
            InlineKeyboardButton(text="✖️ Cancel", callback_data="accounts_menu"),
        ],
    ])
    await safe_edit(callback.message, "Are you sure you want to delete this account?", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("acc_delete_yes:"))
async def acc_delete_apply(callback: CallbackQuery) -> None:
    """
    Реальное удаление после подтверждения.
    """
    user_id = callback.from_user.id
    try:
        acc_id = int(callback.data.split(":", 1)[1])
    except Exception:
        await callback.answer("Bad account id", show_alert=True)
        return

    async with async_session() as session:
        # если удаляем дефолтный — переведём default на другой, если он есть
        st = await session.get(BotSettings, user_id)
        if st and st.default_account_id == acc_id:
            other = (await session.execute(
                select(Account).where(Account.tg_user_id == user_id, Account.id != acc_id).order_by(Account.id)
            )).scalars().first()
            st.default_account_id = other.id if other else None

        await session.execute(delete(Account).where(Account.id == acc_id, Account.tg_user_id == user_id))
        await session.commit()

    await callback.answer("Deleted.")
    await _render_accounts_list(callback.message, user_id)
