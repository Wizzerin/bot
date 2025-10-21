# app/services/notifications.py
from __future__ import annotations
from typing import Optional
import logging

from aiogram import Bot
from app.database.models import async_session, BotSettings

logger = logging.getLogger(__name__)  # app.services.notifications

_BOT: Optional[Bot] = None


def bind_bot(bot: Bot) -> None:
    """Сохраняем Bot для отправки уведомлений из сервисов."""
    global _BOT
    _BOT = bot
    logger.info("notifications.bind_bot: Bot instance bound")


async def notify_user(tg_user_id: int, text: str) -> None:
    """
    Отправить сообщение пользователю/в выбранный им чат.
    • если BotSettings.notify_chat_id есть — туда,
    • иначе — в личку пользователю.
    Ошибки подавляем (сервис фоновый).
    """
    if _BOT is None:
        logger.warning("notify_user: BOT is not bound, skip message")
        return

    target_chat_id = None
    try:
        async with async_session() as session:
            st = await session.get(BotSettings, tg_user_id)
            if st and getattr(st, "notify_chat_id", None):
                target_chat_id = st.notify_chat_id
                logger.debug("notify_user: using notify_chat_id=%s for user=%s", target_chat_id, tg_user_id)
    except Exception as e:
        logger.exception("notify_user: failed to fetch BotSettings for user=%s: %s", tg_user_id, e)

    if target_chat_id is None:
        target_chat_id = tg_user_id
        logger.debug("notify_user: fallback to DM user=%s", tg_user_id)

    try:
        await _BOT.send_message(target_chat_id, text)
        logger.info("notify_user: sent to chat_id=%s (user=%s)", target_chat_id, tg_user_id)
    except Exception as e:
        logger.warning("notify_user: send_message failed to chat_id=%s (user=%s): %s", target_chat_id, tg_user_id, e)
