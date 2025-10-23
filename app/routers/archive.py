# app/routers/archive.py
# ------------------------------------------------------------
# (ИЗМЕНЕНИЕ) Логика Gemini API вынесена в app/services/ai_assistant.py
# ------------------------------------------------------------

import logging
from html import escape
from datetime import datetime
from collections import defaultdict
from dateutil import parser
from typing import Iterable, List, Dict, Optional
from aiogram import Router, F, types
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func, and_

from app.database.models import async_session, PublishedPost, Account
from app.services.safe_edit import safe_edit
from app.keyboards import (
    archive_dates_kb, archive_posts_kb, archive_post_detail_kb,
    archive_comments_kb, archive_comment_reply_kb, archive_confirm_reply_kb,
    archive_import_account_kb, archive_import_list_kb
)

from app.services.threads_client import (
    get_post_metrics, get_post_comments, post_reply, get_user_media,
    ThreadsError, ThreadsAPIError
)
# --- (ИЗМЕНЕНИЕ) Импортируем новую функцию ---
from app.services.ai_assistant import generate_reply_with_gemini
# ---

import httpx # httpx и json больше не нужны здесь напрямую
# import json # <--- Удалено

log = logging.getLogger(__name__)
router = Router()

class ArchiveFSM(StatesGroup):
    viewing_comments = State()
    replying_to_comment = State()
    awaiting_manual_reply = State()
    confirming_ai_reply = State()
    awaiting_edited_reply = State()
    selecting_import_account = State()
    importing_post = State()

COMMENTS_PER_PAGE = 5

# --- (ИЗМЕНЕНИЕ) Функция generate_reply_with_gemini удалена отсюда ---

# =========================
#      ARCHIVE LOGIC
# =========================
# ... (весь остальной код файла без изменений) ...
@router.callback_query(F.data.startswith("archive_list:"))
async def archive_list_dates(cb: CallbackQuery, state: FSMContext):
    """Displays publication dates."""
    await state.clear()
    user_id = cb.from_user.id
    async with async_session() as session:
        result = await session.execute(
            select(
                func.date(PublishedPost.published_at),
                func.count(PublishedPost.id)
            )
            .where(PublishedPost.tg_user_id == user_id)
            .group_by(func.date(PublishedPost.published_at))
            .order_by(func.date(PublishedPost.published_at).desc())
        )
        dates_with_counts = result.all()

    if not dates_with_counts:
        await safe_edit(
            cb.message,
            "<b>🗄️ Archive is empty</b>\n\nYou haven't published any posts via the bot yet.",
            reply_markup=archive_dates_kb([])
        )
        await cb.answer(); return

    await safe_edit(
        cb.message,
        "🗄️ **Published Posts Archive**\n\nSelect a date to view posts or import a new one:",
        reply_markup=archive_dates_kb(dates_with_counts)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("archive_date:"))
async def archive_list_posts_by_date(cb: CallbackQuery, state: FSMContext):
    """Shows posts for the selected date."""
    await state.clear()
    date_str = cb.data.split(":", 1)[1]
    user_id = cb.from_user.id
    async with async_session() as session:
        posts = (await session.execute(
            select(PublishedPost)
            .where(
                PublishedPost.tg_user_id == user_id,
                func.date(PublishedPost.published_at) == date_str
            )
            .order_by(PublishedPost.published_at.desc())
        )).scalars().all()

    if not posts:
        await cb.answer("No posts found for this date.", show_alert=True)
        return

    acc_ids = {p.account_id for p in posts}
    accounts_map = {}
    if acc_ids:
        accs = (await session.execute(select(Account).where(Account.id.in_(acc_ids)))).scalars().all()
        accounts_map = {a.id: a.title or f"id={a.id}" for a in accs}

    # Format date nicely for the title, e.g., "Posts from Oct 22, 2025"
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date()
        formatted_date_title = date_obj.strftime('%b %d, %Y')
    except ValueError:
        formatted_date_title = date_str # Fallback

    await safe_edit(
        cb.message,
        f"🗓️ **Posts from {formatted_date_title}**\n\nSelect a post to view details:",
        reply_markup=archive_posts_kb(posts, accounts_map, date_str)
    )
    await cb.answer()


@router.callback_query(F.data.startswith("archive_post:"))
async def archive_view_post(cb: CallbackQuery, state: FSMContext):
    """Shows post details."""
    await state.clear()
    try:
        post_db_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid post ID.", show_alert=True); return

    user_id = cb.from_user.id
    async with async_session() as session:
        post = await session.get(PublishedPost, post_db_id)
        if not post or post.tg_user_id != user_id:
            await cb.answer("Post not found.", show_alert=True); return

        acc = await session.get(Account, post.account_id)
        acc_title = acc.title if acc else "Unknown"

    date_str = post.published_at.strftime('%Y-%m-%d')
    time_str = post.published_at.strftime('%H:%M:%S')
    media_status = "✅ Yes" if post.has_media else "❌ No"

    # Enhanced formatting
    text = (
        f"📄 **Post Details**\n\n"
        f"👤 **Account:** `{escape(acc_title)}`\n"
        f"🗓️ **Published:** {post.published_at.strftime('%d %b %Y')} at {time_str}\n"
        f"🖼️ **Media:** {media_status}\n" # Changed from Images/Videos
        f"🆔 **Threads ID:** `{escape(post.threads_post_id)}`\n\n"
        f"📝 **Text:**\n{escape(post.text or '(No text)')}"
    )

    await safe_edit(cb.message, text, reply_markup=archive_post_detail_kb(date_str, post_db_id))
    await cb.answer()


@router.callback_query(F.data.startswith("archive_get_stats:"))
async def archive_get_post_stats(cb: CallbackQuery, state: FSMContext):
    """Gets and displays post stats from Threads API."""
    try:
        post_db_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer("Invalid post ID.", show_alert=True); return

    user_id = cb.from_user.id
    async with async_session() as session:
        post = await session.get(PublishedPost, post_db_id)
        if not post or post.tg_user_id != user_id:
            await cb.answer("Post not found.", show_alert=True); return

        acc = await session.get(Account, post.account_id)
        if not acc or not acc.access_token:
            await cb.answer("Account token not found.", show_alert=True); return

        acc_title = acc.title if acc else "Unknown"
        access_token = acc.access_token
        threads_post_id = post.threads_post_id

    await cb.answer("📊 Requesting statistics...")

    stats_text = ""
    try:
        metrics = await get_post_metrics(access_token, threads_post_id)
        # Enhanced formatting
        stats_text = (
            f"\n\n📊 **Statistics:**\n"
            f"  ❤️ Likes: `{metrics.get('likes', 'N/A')}`\n" # Use code block for numbers
            f"  💬 Replies: `{metrics.get('replies', 'N/A')}`"
        )
    except ThreadsAPIError as e:
        stats_text = f"\n\n📊 **Statistics:**\n  ⚠️ _{escape(str(e))}_"
        log.warning("Failed to get stats for post %s (db_id %s): %s", threads_post_id, post_db_id, e)
    except Exception as e_unexp:
        stats_text = f"\n\n📊 **Statistics:**\n  ⚠️ _Unexpected error_"
        log.exception("Unexpected error getting stats for post %s (db_id %s): %s", threads_post_id, post_db_id, e_unexp)

    date_str = post.published_at.strftime('%Y-%m-%d')
    time_str = post.published_at.strftime('%H:%M:%S')
    media_status = "✅ Yes" if post.has_media else "❌ No"

    full_text = (
        f"📄 **Post Details**\n\n"
        f"👤 **Account:** `{escape(acc_title)}`\n"
        f"🗓️ **Published:** {post.published_at.strftime('%d %b %Y')} at {time_str}\n"
        f"🖼️ **Media:** {media_status}\n"
        f"🆔 **Threads ID:** `{escape(post.threads_post_id)}`\n\n"
        f"📝 **Text:**\n{escape(post.text or '(No text)')}"
        f"{stats_text}" # Append formatted stats
    )

    await safe_edit(cb.message, full_text, reply_markup=archive_post_detail_kb(date_str, post_db_id))


# --- Логика комментариев ---

@router.callback_query(F.data.startswith("archive_view_comments:"))
async def archive_view_comments_start(cb: CallbackQuery, state: FSMContext):
    """Handles 'View Comments' button press (starts pagination)."""
    try:
        post_db_id = int(cb.data.split(":", 1)[1])
        cursor = None # Start from the beginning
    except (ValueError, IndexError):
        await cb.answer("Invalid post ID.", show_alert=True); return

    if not await state.get_state():
         await state.clear()

    # (ИЗМЕНЕНИЕ) Инициализируем пагинацию в state
    await state.update_data(
        pagination_cursors={1: None}, # Page 1 starts with no 'after' cursor
        current_page=1
    )
    await _show_comments_page(cb, state, post_db_id, page_to_show=1)


@router.callback_query(F.data.startswith("archive_comments_page:"))
async def archive_view_comments_page(cb: CallbackQuery, state: FSMContext):
    """Handles pagination button press for comments ('prev' or 'next')."""
    try:
        _, post_db_id_str, direction = cb.data.split(":", 2)
        post_db_id = int(post_db_id_str)
    except (ValueError, IndexError):
        await cb.answer("Invalid pagination data.", show_alert=True); return

    current_data = await state.get_data()
    current_page = current_data.get('current_page', 1)

    page_to_show = current_page
    if direction == 'next':
        page_to_show += 1
    elif direction == 'prev':
        page_to_show -= 1
    else:
        await cb.answer("Unknown pagination direction.", show_alert=True)
        return

    if page_to_show < 1:
        await cb.answer("You are already on the first page.", show_alert=True)
        return

    # Check if we have the cursor for the target page
    pagination_cursors = current_data.get('pagination_cursors', {1: None})
    if page_to_show not in pagination_cursors:
        # This shouldn't happen if 'Next'/'Prev' buttons are shown correctly,
        # but as a fallback, prevent going to an unknown page.
        await cb.answer("Cannot navigate to this page yet.", show_alert=True)
        return

    await _show_comments_page(cb, state, post_db_id, page_to_show=page_to_show)


async def _show_comments_page(cb: CallbackQuery, state: FSMContext, post_db_id: int, page_to_show: int):
    """Helper function to fetch and display a specific page of comments."""
    user_id = cb.from_user.id
    async with async_session() as session:
        post = await session.get(PublishedPost, post_db_id)
        if not post or post.tg_user_id != user_id:
            await cb.answer("Post not found.", show_alert=True); return
        acc = await session.get(Account, post.account_id)
        if not acc or not acc.access_token:
            await cb.answer("Account token not found.", show_alert=True); return
        access_token = acc.access_token
        threads_post_id = post.threads_post_id
        date_str = post.published_at.strftime('%Y-%m-%d')

    current_data = await state.get_data()
    pagination_cursors = current_data.get('pagination_cursors', {1: None})
    # Get the 'after' cursor needed to fetch *this* page (it's stored under this page's number)
    cursor_for_this_page_request = pagination_cursors.get(page_to_show)

    await cb.answer(f"Loading page {page_to_show}...")
    try:
        # Fetch comments using the correct cursor for this page
        comments_data = await get_post_comments(
            access_token, threads_post_id, limit=COMMENTS_PER_PAGE, after=cursor_for_this_page_request
        )
        comments = comments_data.get("data", [])
        paging_info = comments_data.get("paging", {})
        next_cursor = paging_info.get("cursors", {}).get("after")

        # Store the cursor needed to fetch the *next* page
        if next_cursor:
            pagination_cursors[page_to_show + 1] = next_cursor
        # Remove cursor for pages beyond the next one if 'next_cursor' is None (reached the end)
        elif page_to_show + 1 in pagination_cursors:
             del pagination_cursors[page_to_show + 1]


        has_next_page = bool(next_cursor)
        has_prev_page = page_to_show > 1

        await state.set_state(ArchiveFSM.viewing_comments)
        await state.update_data(
            post_db_id=post_db_id,
            threads_post_id=threads_post_id,
            date_str=date_str,
            current_page=page_to_show,
            pagination_cursors=pagination_cursors,
            current_comments_cache=comments
        )

        text = f"🗣️ **Comments for post:**\n`{escape(post.text[:50])}...`\n\n(Page {page_to_show})"
        if not comments:
            text += "\n\n_No more comments found._" if page_to_show > 1 else "\n\n_No comments found yet._"

        await safe_edit(
            cb.message,
            text,
            reply_markup=archive_comments_kb(
                comments, post_db_id, date_str, page_to_show, COMMENTS_PER_PAGE,
                has_next_page=has_next_page,
                has_prev_page=has_prev_page
            )
        )

    except ThreadsAPIError as e:
        await safe_edit(cb.message, f"⚠️ Failed to load comments: {escape(str(e))}",
                        reply_markup=archive_post_detail_kb(date_str, post_db_id))
        log.warning("Failed to get comments for post %s (db_id %s): %s", threads_post_id, post_db_id, e)
        await state.clear()
    except Exception as e_unexp:
        await safe_edit(cb.message, "⚠️ Failed to load comments: Unexpected error.",
                         reply_markup=archive_post_detail_kb(date_str, post_db_id))
        log.exception("Unexpected error getting comments for post %s (db_id %s): %s", threads_post_id, post_db_id, e_unexp)
        await state.clear()


@router.callback_query(ArchiveFSM.viewing_comments, F.data.startswith("archive_select_comment:"))
async def archive_select_comment(cb: CallbackQuery, state: FSMContext):
    """Handles clicking on a specific comment button."""
    try:
        _, post_db_id_str, comment_id = cb.data.split(":", 2)
        post_db_id = int(post_db_id_str)
    except (ValueError, IndexError):
        await cb.answer("Invalid comment data.", show_alert=True); return

    fsm_data = await state.get_data()
    comments_cache = fsm_data.get("current_comments_cache", [])

    original_comment_text = "N/A"
    original_comment_user = "Unknown"

    comment = next((c for c in comments_cache if c.get("id") == comment_id), None)

    if comment:
        original_comment_text = comment.get("text", "")
        original_comment_user = comment.get("username", "Unknown")
    else:
        log.warning("Comment ID %s not found in FSM cache. State: %s", comment_id, fsm_data)
        await cb.answer("Could not find comment details in session. Please go back and try again.", show_alert=True)
        return

    user_id = cb.from_user.id
    acc_title = "My Account" # Fallback
    async with async_session() as session:
        post = await session.get(PublishedPost, post_db_id)
        if post and post.tg_user_id == user_id:
             acc = await session.get(Account, post.account_id)
             if acc: acc_title = acc.title or f"id={acc.id}"

    await state.set_state(ArchiveFSM.replying_to_comment)
    await state.update_data(
        reply_to_comment_id=comment_id,
        original_comment_text=original_comment_text,
        original_comment_user=original_comment_user,
        account_title=acc_title
    )

    text = (
        f"💬 **Replying to:**\n"
        f"  👤 `{escape(original_comment_user)}`\n"
        f"  📝 _{escape(original_comment_text)}_\n\n"
        f"Choose an action:"
    )
    await safe_edit(cb.message, text, reply_markup=archive_comment_reply_kb(post_db_id, comment_id))
    await cb.answer()


@router.callback_query(ArchiveFSM.replying_to_comment, F.data.startswith("archive_generate_reply:"))
@router.callback_query(ArchiveFSM.confirming_ai_reply, F.data.startswith("archive_generate_reply:"))
async def archive_generate_reply(cb: CallbackQuery, state: FSMContext):
    """Generates AI reply draft (also handles Regenerate button)."""
    try:
        _, post_db_id_str, comment_id = cb.data.split(":", 2)
        post_db_id = int(post_db_id_str)
    except (ValueError, IndexError):
        await cb.answer("Invalid data.", show_alert=True); return

    fsm_data = await state.get_data()
    original_comment_text = fsm_data.get("original_comment_text", "...")
    original_comment_user = fsm_data.get("original_comment_user", "Unknown")
    account_title = fsm_data.get("account_title", "My Account")

    await cb.answer("🤖 Generating AI reply...")
    ai_draft = await generate_reply_with_gemini(original_comment_text, account_title)

    await state.set_state(ArchiveFSM.confirming_ai_reply)
    await state.update_data(ai_reply_draft=ai_draft)

    text = (
        f"💬 **Replying to:**\n"
        f"  👤 `{escape(original_comment_user)}`\n"
        f"  📝 _{escape(original_comment_text)}_\n\n"
        f"🤖 **AI Draft Reply:**\n{escape(ai_draft)}\n\n"
        f"Publish this reply?"
    )
    await safe_edit(cb.message, text, reply_markup=archive_confirm_reply_kb(post_db_id, comment_id))


@router.callback_query(ArchiveFSM.replying_to_comment, F.data.startswith("archive_write_reply:"))
async def archive_write_reply_start(cb: CallbackQuery, state: FSMContext):
    """Asks user to write reply manually."""
    await state.set_state(ArchiveFSM.awaiting_manual_reply)
    fsm_data = await state.get_data()
    original_comment_user = fsm_data.get("original_comment_user", "Unknown")

    await safe_edit(cb.message, f"✏️ Please send your reply text for `{escape(original_comment_user)}`.\n\n/cancel to abort.")
    await cb.answer()


@router.message(ArchiveFSM.awaiting_manual_reply)
async def archive_handle_manual_reply(message: Message, state: FSMContext):
    """Handles the manually entered reply text."""
    reply_text = message.text
    if not reply_text or reply_text.lower() == '/cancel':
        await message.answer("Cancelled.", reply_markup=types.ReplyKeyboardRemove())
        fsm_data = await state.get_data()
        post_db_id = fsm_data.get("post_db_id")
        current_page = fsm_data.get("current_page", 1) # Get current page to return to
        if post_db_id:
             # Имитируем нажатие кнопки для возврата к текущей странице
             fake_cb = types.CallbackQuery(id='fake_cancel_manual', from_user=message.from_user, message=message, chat_instance='fake_cancel_manual', data=f"archive_view_comments:{post_db_id}")
             # Need to find the original message to edit back
             # This is tricky without storing message_id in state.
             # Simplification: Go back to the main post detail view
             await archive_view_post(fake_cb, state) # Re-use fake_cb, data doesn't matter much here
             # await _show_comments_page(fake_cb, state, post_db_id, page_to_show=current_page) # This would require storing the message_id to edit
        else:
             await state.clear()
        return

    fsm_data = await state.get_data()
    post_db_id = fsm_data.get("post_db_id")
    comment_id = fsm_data.get("reply_to_comment_id")
    current_page = fsm_data.get("current_page", 1)

    if not all([post_db_id, comment_id]):
        log.error("Missing data in state for manual reply: %s", fsm_data)
        await message.answer("❌ Error: Could not retrieve necessary data. Please try again.")
        await state.clear()
        return

    wait_msg = await message.answer("⏳ Publishing reply...")
    try:
        access_token = await _get_token_for_post(state, message.from_user.id, post_db_id)
        if not access_token:
             await safe_edit(wait_msg, "❌ Error: Could not retrieve account token.")
             await state.clear()
             return

        await post_reply(access_token=access_token, text=reply_text, reply_to_id=comment_id)
        await safe_edit(wait_msg, "✅ Reply published successfully!")

        # Возвращаемся к списку комментариев (на ту же страницу)
        fake_cb = types.CallbackQuery(id='fake_reply_manual', from_user=message.from_user, message=wait_msg, chat_instance='fake_reply_manual', data=f"archive_view_comments:{post_db_id}")
        await _show_comments_page(fake_cb, state, post_db_id, page_to_show=current_page)

    except ThreadsAPIError as e:
        log.warning("Failed to publish manual reply to %s: %s", comment_id, e)
        await safe_edit(wait_msg, f"❌ Failed to publish reply: {escape(str(e))}\n\nPlease try again or /cancel.")
    except Exception as e_unexp:
        log.exception("Unexpected error publishing manual reply to %s: %s", comment_id, e_unexp)
        await safe_edit(wait_msg, f"❌ Failed to publish reply: Unexpected error.")
        await state.clear()


@router.callback_query(ArchiveFSM.confirming_ai_reply, F.data.startswith("archive_publish_reply:"))
async def archive_publish_ai_reply(cb: CallbackQuery, state: FSMContext):
    """Publishes the AI-generated reply."""
    fsm_data = await state.get_data()
    post_db_id = fsm_data.get("post_db_id")
    comment_id = fsm_data.get("reply_to_comment_id")
    ai_draft = fsm_data.get("ai_reply_draft")
    current_page = fsm_data.get("current_page", 1)

    if not all([post_db_id, comment_id, ai_draft]):
        log.error("Missing data in state for publishing AI reply: %s", fsm_data)
        await cb.answer("❌ Error: Could not retrieve necessary data. Please try again.", show_alert=True)
        await state.clear()
        return

    await cb.answer("⏳ Publishing AI reply...")
    try:
        access_token = await _get_token_for_post(state, cb.from_user.id, post_db_id)
        if not access_token:
             await safe_edit(cb.message, "❌ Error: Could not retrieve account token.")
             await state.clear()
             return

        await post_reply(access_token=access_token, text=ai_draft, reply_to_id=comment_id)
        await safe_edit(cb.message, "✅ AI Reply published successfully!")

        # Возвращаемся к списку комментариев (на ту же страницу)
        await _show_comments_page(cb, state, post_db_id, page_to_show=current_page)

    except ThreadsAPIError as e:
        log.warning("Failed to publish AI reply to %s: %s", comment_id, e)
        # Append error to the existing message text
        current_text = cb.message.text or ""
        error_text = f"\n\n❌ **Failed to publish:** _{escape(str(e))}_"
        await safe_edit(cb.message, current_text + error_text,
                        reply_markup=archive_confirm_reply_kb(post_db_id, comment_id))
        await cb.answer() # Сбросить "загрузку"
    except Exception as e_unexp:
        log.exception("Unexpected error publishing AI reply to %s: %s", comment_id, e_unexp)
        await safe_edit(cb.message, f"❌ Failed to publish AI reply: Unexpected error.")
        await state.clear()


@router.callback_query(ArchiveFSM.confirming_ai_reply, F.data.startswith("archive_edit_reply:"))
async def archive_edit_ai_reply_start(cb: CallbackQuery, state: FSMContext):
    """Asks user to edit the AI reply."""
    fsm_data = await state.get_data()
    ai_draft = fsm_data.get("ai_reply_draft", "")
    await state.set_state(ArchiveFSM.awaiting_edited_reply)
    await safe_edit(cb.message, f"✏️ Send your edited version of the reply:\n\n`{escape(ai_draft)}`\n\n/cancel to abort.")
    await cb.answer()


@router.message(ArchiveFSM.awaiting_edited_reply)
async def archive_handle_edited_reply(message: Message, state: FSMContext):
    """Handles the edited AI reply text."""
    edited_reply_text = message.text
    if not edited_reply_text or edited_reply_text.lower() == '/cancel':
        # Вернуться к экрану подтверждения ИИ-ответа
        fsm_data = await state.get_data()
        post_db_id = fsm_data.get("post_db_id")
        comment_id = fsm_data.get("reply_to_comment_id")
        original_comment_text = fsm_data.get("original_comment_text", "...")
        original_comment_user = fsm_data.get("original_comment_user", "Unknown")
        ai_draft = fsm_data.get("ai_reply_draft", "...")

        if post_db_id and comment_id:
             await state.set_state(ArchiveFSM.confirming_ai_reply)
             text = (
                 f"💬 **Replying to:**\n"
                 f"  👤 `{escape(original_comment_user)}`\n"
                 f"  📝 _{escape(original_comment_text)}_\n\n"
                 f"🤖 **AI Draft Reply:**\n{escape(ai_draft)}\n\n"
                 f"What should I do? (Cancelled editing)"
             )
             # Simplification: Send a new message instead of trying complex edits.
             await message.answer(text, reply_markup=archive_confirm_reply_kb(post_db_id, comment_id))
        else:
             await message.answer("Cancelled.")
             await state.clear()
        return

    fsm_data = await state.get_data()
    post_db_id = fsm_data.get("post_db_id")
    comment_id = fsm_data.get("reply_to_comment_id")
    current_page = fsm_data.get("current_page", 1)

    if not all([post_db_id, comment_id]):
        log.error("Missing data in state for edited reply: %s", fsm_data)
        await message.answer("❌ Error: Could not retrieve necessary data. Please try again.")
        await state.clear()
        return

    wait_msg = await message.answer("⏳ Publishing edited reply...")
    try:
        access_token = await _get_token_for_post(state, message.from_user.id, post_db_id)
        if not access_token:
             await safe_edit(wait_msg, "❌ Error: Could not retrieve account token.")
             await state.clear()
             return

        await post_reply(access_token=access_token, text=edited_reply_text, reply_to_id=comment_id)
        await safe_edit(wait_msg, "✅ Edited reply published successfully!")

        # Возвращаемся к списку комментариев (на ту же страницу)
        fake_cb = types.CallbackQuery(id='fake_edited_reply', from_user=message.from_user, message=wait_msg, chat_instance='fake_edited_reply', data=f"archive_view_comments:{post_db_id}")
        await _show_comments_page(fake_cb, state, post_db_id, page_to_show=current_page)

    except ThreadsAPIError as e:
        log.warning("Failed to publish edited reply to %s: %s", comment_id, e)
        await safe_edit(wait_msg, f"❌ Failed to publish edited reply: {escape(str(e))}\n\nPlease try again or /cancel.")
    except Exception as e_unexp:
        log.exception("Unexpected error publishing edited reply to %s: %s", comment_id, e_unexp)
        await safe_edit(wait_msg, f"❌ Failed to publish edited reply: Unexpected error.")
        await state.clear()


@router.callback_query(ArchiveFSM.confirming_ai_reply, F.data.startswith("archive_select_comment:"))
async def archive_cancel_ai_reply(cb: CallbackQuery, state: FSMContext):
    """Cancels AI reply and returns to the comment action menu. (Handles 'Cancel' button)"""
    # Callback data format is archive_select_comment:{post_db_id}:{comment_id}
    # This correctly triggers archive_select_comment, which resets the state
    await archive_select_comment(cb, state)


@router.callback_query(ArchiveFSM.replying_to_comment, F.data.startswith("archive_view_comments:"))
async def archive_reply_back_to_comments(cb: CallbackQuery, state: FSMContext):
     """Handles 'Back to Comments' from the reply action menu."""
     # Callback data format is archive_view_comments:{post_db_id}
     # This correctly triggers archive_view_comments_start, which resets the state
     await archive_view_comments_start(cb, state)

@router.callback_query(F.data == "noop")
async def noop_callback(cb: CallbackQuery):
    await cb.answer()

async def _get_token_for_post(state: FSMContext, user_id: int, post_db_id: int) -> Optional[str]:
    """Helper to get access token based on post_db_id."""
    fsm_data = await state.get_data()
    # Check if we got token during import process
    if 'account_id_for_import' in fsm_data:
         # Need to fetch the token again as it might not be in state
         async with async_session() as session:
              acc = await session.get(Account, fsm_data['account_id_for_import'])
              if acc and acc.tg_user_id == user_id:
                   return acc.access_token
         log.warning("Could not refetch access token for imported post context (user %d, acc %s)", user_id, fsm_data.get('account_id_for_import'))
         return None


    # Otherwise, fetch from DB based on the post's account_id
    async with async_session() as session:
        post = await session.get(PublishedPost, post_db_id)
        if post and post.tg_user_id == user_id:
            acc = await session.get(Account, post.account_id)
            if acc:
                return acc.access_token
    log.warning("Could not find access token for user %d and post %d", user_id, post_db_id)
    return None

# --- (ИМПОРТ ПОСТОВ) ---
# ... (код импорта без изменений) ...
@router.callback_query(F.data == "archive_import_start")
async def archive_import_start(cb: CallbackQuery, state: FSMContext):
    """Starts the import process: asks to select an account if multiple exist."""
    user_id = cb.from_user.id
    async with async_session() as session:
        accounts = (await session.execute(
            select(Account).where(Account.tg_user_id == user_id).order_by(Account.id)
        )).scalars().all()
    if not accounts:
        await cb.answer("You have no accounts to import from.", show_alert=True); return
    if len(accounts) == 1:
        await state.set_state(ArchiveFSM.importing_post)
        await state.update_data(account_id_for_import=accounts[0].id, access_token_for_import=accounts[0].access_token)
        await _fetch_and_show_posts(cb, state)
    else:
        await state.set_state(ArchiveFSM.selecting_import_account)
        await safe_edit(cb.message, "📥 **Import Post**\n\nSelect an account...", reply_markup=archive_import_account_kb(accounts))
        await cb.answer()

@router.callback_query(ArchiveFSM.selecting_import_account, F.data.startswith("archive_import_acc:"))
async def archive_select_import_account(cb: CallbackQuery, state: FSMContext):
    """Handles account selection for import."""
    try: acc_id = int(cb.data.split(":", 1)[1])
    except: await cb.answer("Invalid account.", show_alert=True); return
    async with async_session() as session:
        acc = await session.get(Account, acc_id)
        if not acc or acc.tg_user_id != cb.from_user.id:
            await cb.answer("Account not found.", show_alert=True); return
        await state.set_state(ArchiveFSM.importing_post)
        await state.update_data(account_id_for_import=acc.id, access_token_for_import=acc.access_token)
    await _fetch_and_show_posts(cb, state)

async def _fetch_and_show_posts(cb: CallbackQuery, state: FSMContext):
    """Fetches user's recent Threads media and displays them for selection."""
    fsm_data = await state.get_data()
    access_token = fsm_data.get("access_token_for_import")
    if not access_token:
        await cb.answer("Error: Account token not found.", show_alert=True); await state.clear(); return
    await cb.answer("Fetching recent posts...")
    try:
        posts_data = await get_user_media(access_token, limit=10) # Get latest 10 posts
        posts = posts_data.get("data", [])
        if not posts:
            await safe_edit(cb.message, "No recent posts found on this account.", reply_markup=archive_dates_kb([])); await state.clear(); return

        # Store fetched posts in state to use when user selects one
        await state.update_data(fetched_posts=posts)
        await safe_edit(cb.message, "📥 **Import Post**\n\nSelect a post to add to archive:", reply_markup=archive_import_list_kb(posts))
    except Exception as e:
        log.warning("Failed to get user media: %s", e); await safe_edit(cb.message, f"❌ Failed: {escape(str(e))}", reply_markup=archive_dates_kb([])); await state.clear()

@router.callback_query(ArchiveFSM.importing_post, F.data.startswith("archive_import_select:"))
async def archive_import_select(cb: CallbackQuery, state: FSMContext):
    """Handles post selection and saves it to the archive DB."""
    try: threads_post_id = cb.data.split(":", 1)[1]
    except: await cb.answer("Invalid post ID.", show_alert=True); return

    fsm_data = await state.get_data(); fetched_posts = fsm_data.get("fetched_posts", []); account_id = fsm_data.get("account_id_for_import"); user_id = cb.from_user.id
    if not account_id or not fetched_posts:
        await cb.answer("Error: Session expired.", show_alert=True); await state.clear(); await archive_list_dates(cb, state); return

    post_to_import = next((p for p in fetched_posts if p.get("id") == threads_post_id), None)
    if not post_to_import:
        await cb.answer("Error: Post details not found in session.", show_alert=True); return

    async with async_session() as session:
        exists = (await session.execute(select(PublishedPost.id).where(and_(PublishedPost.tg_user_id == user_id, PublishedPost.threads_post_id == threads_post_id)))).scalars().first()
        if exists:
            await cb.answer("This post is already in the archive.", show_alert=True); return

        try:
            published_dt_str = post_to_import.get('timestamp')
            # Ensure timestamp is timezone-aware (assuming UTC from API) before saving
            published_dt = parser.parse(published_dt_str).replace(tzinfo=None) if published_dt_str else datetime.utcnow()
        except Exception:
            log.warning("Could not parse timestamp %s", post_to_import.get('timestamp')); published_dt = datetime.utcnow()

        media_type = post_to_import.get('media_type');
        has_media = media_type in ('IMAGE', 'CAROUSEL', 'VIDEO')

        new_post = PublishedPost(
            tg_user_id=user_id,
            account_id=account_id,
            threads_post_id=threads_post_id,
            text=post_to_import.get('text'),
            published_at=published_dt,
            has_media=has_media
        )
        session.add(new_post); await session.commit()
        await cb.answer("✅ Post imported!", show_alert=True)

        # Go back to the main archive view
        await state.clear()
        # Need to call archive_list_dates again to show the updated list
        await archive_list_dates(cb, state)

