# app/middleware/throttling.py
# ------------------------------------------------------------
# Middleware для защиты от флуда (слишком частых запросов).
# Если пользователь отправляет запросы чаще, чем раз в N секунд,
# бот будет игнорировать их, предотвращая перегрузку.
# ------------------------------------------------------------

from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, User
from cachetools import TTLCache

# Кеш для хранения времени последнего сообщения от пользователя.
# `ttl` — время жизни записи в секундах.
# В данном случае, мы не будем отвечать пользователю чаще, чем раз в 0.7 секунды.
THROTTLE_TIME = 0.7
user_cache = TTLCache(maxsize=10_000, ttl=THROTTLE_TIME)


class ThrottlingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message | CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        
        # (ИЗМЕНЕНО) Получаем объект пользователя из данных события.
        # Это более надежный способ, чем event.from_user, так как он работает
        # для разных типов обновлений (сообщения, колбеки и т.д.).
        user: User | None = data.get('event_from_user')

        # Если событие не от пользователя, то не применяем ограничение
        if not user:
            return await handler(event, data)

        user_id = user.id

        # Проверяем, есть ли пользователь в кеше
        if user_id in user_cache:
            # Если есть, значит, он отправляет запросы слишком часто, игнорируем
            return
        
        # Если пользователя нет в кеше, добавляем его
        user_cache[user_id] = None
        
        # Передаем управление дальше, к хэндлерам
        return await handler(event, data)

