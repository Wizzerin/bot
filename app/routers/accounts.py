# app/routers/accounts.py
# ------------------------------------------------------------
# ЕДИНЫЙ роутер для управления аккаунтами и токенами Threads.
# ------------------------------------------------------------

import logging
from html import escape

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from sqlalchemy import select, delete, update

from app.database.models import async_session, Account
from app.services.safe_edit import safe_edit
from app.services.threads_client import get_profile as get_threads_profile, ThreadsError
from app.services.token_health import check_and_cache_token_health
from app.keyboards import accounts_menu_kb, account_actions_kb, account_delete_confirm_kb

log = logging.getLogger(__name__)
router = Router()

# (ИЗМЕНЕНО) Лог для проверки загрузки роутера
log.info("Accounts router loaded!")


# ---------- FSMs ----------
class SetTokenFSM(StatesGroup):
    waiting_token = State()

class RenameAccountFSM(StatesGroup):
    waiting_new_name = State()


# ===============================================
#   ГЛАВНОЕ МЕНЮ РАЗДЕЛА
# ===============================================

# --- НОВЫЙ ХЕНДЛЕР ---
# Этот хендлер перехватывает нажатие на reply-кнопку "🔑 Accounts"
# и имитирует нажатие inline-кнопки "tok_accounts",
# чтобы повторно использовать существующую логику acc_list_menu.
@router.message(F.text == "🔑 Accounts")
async def handle_accounts_text_button(message: Message):
    """
    Перехватывает нажатие на reply-кнопку "🔑 Accounts"
    и имитирует нажатие inline-кнопки "tok_accounts",
    чтобы повторно использовать существующую логику acc_list_menu.
    """
    
    async def mock_answer(*args, **kwargs):
        # Эта функция-пустышка нужна,
        # т.к. acc_list_menu вызывает cb.answer()
        pass

    # Создаем "фейковый" объект CallbackQuery,
    # чтобы передать его в acc_list_menu
    fake_cb = type("FakeCallbackQuery", (object,), {
        "data": "tok_accounts",
        "message": message, # Используем сообщение от кнопки
        "from_user": message.from_user,
        "answer": mock_answer # Передаем async-пустышку
    })
    
    await acc_list_menu(fake_cb)
# --- КОНЕЦ НОВОГО ХЕНДЛЕРА ---


@router.callback_query(F.data == "tok_accounts")
async def acc_list_menu(cb: CallbackQuery) -> None:
    """Отображает список аккаунтов или меню для их добавления."""
    user_id = cb.from_user.id
    async with async_session() as session:
        accounts = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()

    if not accounts:
        # Если сообщение можно отредактировать, делаем это. Иначе отправляем новое.
        await safe_edit(
            cb.message,
            "You have no accounts yet. Press '🔑 Set token' to add one.",
            reply_markup=accounts_menu_kb([]) # Показываем только кнопку "Set" и "Back"
        )
        await cb.answer(); return

    await safe_edit(cb.message, "Select an account to manage:", reply_markup=accounts_menu_kb(accounts))
    await cb.answer()


# ===============================================
#   ДОБАВЛЕНИЕ/ИЗМЕНЕНИЕ ТОКЕНА
# ===============================================

@router.callback_query(F.data == "tok_set")
async def tok_enter_token_start(cb: CallbackQuery, state: FSMContext) -> None:
    await safe_edit(cb.message, "Send me your Threads token (starts with 'TH...').\n/cancel to cancel.")
    await state.set_state(SetTokenFSM.waiting_token)
    await cb.answer()


@router.message(Command("set_token"))
async def tok_save_token_cmd(message: Message, state: FSMContext) -> None:
    if not message.text or len(message.text.split()) < 2:
        await message.answer("Usage: <code>/set_token TH...</code>")
        return

    token = message.text.split(maxsplit=1)[1]
    await message.delete()
    await _process_and_save_token(message, state, token)


@router.message(SetTokenFSM.waiting_token)
async def tok_enter_token_msg(message: Message, state: FSMContext) -> None:
    token = (message.text or "").strip()
    if not token.startswith("TH"):
        await message.answer("Token should start with 'TH...'. Try again or /cancel.")
        return

    await message.delete()
    await _process_and_save_token(message, state, token)


async def _process_and_save_token(message: Message, state: FSMContext, token: str) -> None:
    """Общая логика проверки и сохранения токена."""
    await state.clear()
    wait_msg = await message.answer("Checking token...")
    
    try:
        profile = await get_threads_profile(token)
        username = profile.get("username", "unknown")
        
        async with async_session() as session:
            new_acc = Account(
                tg_user_id=message.from_user.id,
                access_token=token,
                title=username,
            )
            session.add(new_acc)
            await session.commit()
            
            await check_and_cache_token_health(new_acc.id, notify_on_error=False)

            q = select(Account).where(Account.tg_user_id == message.from_user.id)
            user_accounts = (await session.execute(q)).scalars().all()
            if len(user_accounts) == 1:
                user_accounts[0].is_default = True
                await session.commit()

        await wait_msg.edit_text(f"✅ Token is valid and saved!\nAccount: <b>{username}</b>")

    except ThreadsError as e:
        await wait_msg.edit_text(f"❌ Invalid token: {e}")
    except Exception as e:
        log.exception("Error setting token: %s", e)
        await wait_msg.edit_text("❌ An unexpected error occurred.")


# ===============================================
#   УПРАВЛЕНИЕ КОНКРЕТНЫМ АККАУНТОМ
# ===============================================

@router.callback_query(F.data.startswith("acc_view:"))
async def acc_view_actions(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    try:
        acc_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid account ID.", show_alert=True); return

    async with async_session() as session:
        acc = await session.get(Account, acc_id)

    if not acc or acc.tg_user_id != user_id:
        await cb.answer("Account not found.", show_alert=True); return

    wait_msg = await cb.message.answer("Checking token health...")
    is_healthy, reason = await check_and_cache_token_health(acc_id, notify_on_error=False)
    await wait_msg.delete()
    
    status_icon = "✅" if is_healthy else "❌"
    status_text = "Healthy" if is_healthy else f"Invalid ({reason})"
    default_text = " (default)" if acc.is_default else ""

    text = (
        f"<b>Account:</b> {escape(acc.title or 'N/A')}{default_text}\n"
        f"<b>Status:</b> {status_icon} {status_text}"
    )
    
    await safe_edit(cb.message, text, reply_markup=account_actions_kb(acc_id))
    await cb.answer()


# ---- Удаление ----
@router.callback_query(F.data.startswith("acc_delete:"))
async def acc_delete_confirm(cb: CallbackQuery):
    try:
        acc_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid account ID.", show_alert=True); return

    await safe_edit(
        cb.message,
        "Are you sure you want to delete this account and all its scheduled posts?",
        reply_markup=account_delete_confirm_kb(acc_id)
    )
    await cb.answer()

@router.callback_query(F.data.startswith("acc_delete_confirm:"))
async def acc_delete_do(cb: CallbackQuery):
    user_id = cb.from_user.id
    try:
        acc_id_to_delete = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid account ID.", show_alert=True); return

    async with async_session() as session:
        await session.execute(
            delete(Account)
            .where(Account.id == acc_id_to_delete, Account.tg_user_id == user_id)
        )
        
        remaining_accounts = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()
        
        if remaining_accounts and not any(acc.is_default for acc in remaining_accounts):
            remaining_accounts[0].is_default = True
        
        await session.commit()

    await cb.answer("Account deleted.")
    await acc_list_menu(cb)


# ---- Переименование ----
@router.callback_query(F.data.startswith("acc_rename:"))
async def acc_rename_start(cb: CallbackQuery, state: FSMContext):
    try:
        acc_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid account ID.", show_alert=True); return
    
    await state.set_state(RenameAccountFSM.waiting_new_name)
    await state.update_data(rename_acc_id=acc_id)
    await safe_edit(cb.message, "Send a new name for this account (e.g., 'Work Account').\n/cancel to abort.")
    await cb.answer()

@router.message(RenameAccountFSM.waiting_new_name)
async def acc_rename_finish(message: Message, state: FSMContext):
    new_name = (message.text or "").strip()
    if not new_name or len(new_name) > 50:
        await message.answer("Name cannot be empty or longer than 50 characters. Try again or /cancel.")
        return

    data = await state.get_data()
    acc_id = data.get("rename_acc_id")

    async with async_session() as session:
        await session.execute(
            update(Account)
            .where(Account.id == acc_id, Account.tg_user_id == message.from_user.id)
            .values(title=new_name)
        )
        await session.commit()
    
    await state.clear()
    await message.answer("✅ Account renamed.")
    
    async def mock_answer(*args, **kwargs): pass

    fake_cb = type("FakeCallbackQuery", (object,), {
        "data": f"acc_view:{acc_id}",
        "message": message, 
        "from_user": message.from_user,
        "answer": mock_answer
    })
    
    await acc_list_menu(fake_cb)


# ---- Установка по-умолчанию ----
@router.callback_query(F.data.startswith("acc_set_default:"))
async def acc_set_default(cb: CallbackQuery):
    user_id = cb.from_user.id
    try:
        acc_id_to_set = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid account ID.", show_alert=True); return

    async with async_session() as session:
        # Сначала снимаем флаг "default" со всех аккаунтов
        await session.execute(
            update(Account)
            .where(Account.tg_user_id == user_id)
            .values(is_default=False)
        )
        # Затем ставим "default" нужному
        await session.execute(
            update(Account)
            .where(Account.id == acc_id_to_set, Account.tg_user_id == user_id)
            .values(is_default=True)
        )
        await session.commit()

    await cb.answer("Set as default account.")
    
    # Обновляем текущее меню, чтобы показать "(default)"
    await acc_view_actions(cb)
