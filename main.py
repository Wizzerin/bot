# main.py
# ------------------------------------------------------------
# Точка входа бота (aiogram v3.7+).
# Подключаем только корневой роутер, инициализируем БД и планировщик.
# ------------------------------------------------------------

from __future__ import annotations

import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings
from app.routers import router as root_router
from app.database.init_db import init_db
from app.services.scheduler import init_schedule

# ВАЖНО: привязки бота к сервисам
from app.services import tg_io
from app.services.notifications import bind_bot as bind_notifications_bot


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bot_token = getattr(settings, "TG_BOT_TOKEN", None)
    if not bot_token:
        raise RuntimeError("TG_BOT_TOKEN is not set in settings/.env")

    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Привязываем bot к утилитам, которым нужен живой инстанс
    tg_io.bind_bot(bot)
    bind_notifications_bot(bot)

    # Подключаем корневой роутер
    dp.include_router(root_router)

    # 1) Инициализация БД
    await init_db()

    # 2) Планировщик (APS) + периодический health-check токенов
    await init_schedule(bot, tz="Europe/Berlin")

    # 3) Запуск polling
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")
