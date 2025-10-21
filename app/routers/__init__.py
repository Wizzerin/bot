# app/routers/__init__.py
from __future__ import annotations
from aiogram import Router

from .nav import router as nav_router
from .help import router as help_router
from .accounts import router as accounts_router # (ИЗМЕНЕНО)
from .notify import router as notify_router
from .schedule import router as schedule_router
from .post_now import router as post_now_router
from .tokens_recheck import router as tokens_recheck_router

router = Router()

router.include_router(nav_router)
router.include_router(help_router)
router.include_router(accounts_router) # (ИЗМЕНЕНО)
router.include_router(notify_router)
router.include_router(schedule_router)
router.include_router(post_now_router)
router.include_router(tokens_recheck_router)

