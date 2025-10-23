# app/routers/__init__.py
# ------------------------------------------------------------
# Главный роутер, собирающий все остальные.
# (ИЗМЕНЕНИЕ) Добавлен drafts_router.
# ------------------------------------------------------------

from aiogram import Router

# Импортируем все "под-роутеры"
from .nav import router as nav_router
from .accounts import router as accounts_router
from .post_now import router as post_now_router
from .schedule import router as schedule_router
from .schedule_tools import router as schedule_tools_router # Для DOW, импорта/экспорта
from .notify import router as notify_router
from .timezone import router as timezone_router # Для настроек TZ
from .help import router as help_router
from .archive import router as archive_router # <-- Добавили в прошлый раз
from .drafts import router as drafts_router # <-- НОВЫЙ ИМПОРТ

# Создаем главный роутер
router = Router(name="main-router")

# Подключаем все дочерние роутеры
router.include_router(nav_router)
router.include_router(accounts_router)
router.include_router(post_now_router)
router.include_router(schedule_router)
router.include_router(schedule_tools_router)
router.include_router(notify_router)
router.include_router(timezone_router)
router.include_router(help_router)
router.include_router(archive_router)
router.include_router(drafts_router) # <-- ПОДКЛЮЧАЕМ НОВЫЙ РОУТЕР

