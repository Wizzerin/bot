# app/services/scheduler.py
# ------------------------------------------------------------
# Планировщик публикаций:
#  • init_scheduler(bot)  — запуск APS + периодическая проверка токенов
#  • reload_schedule()    — пересборка Cron-задач из таблицы Job
#  • _run_job(job_id)     — публикация поста (текст + медиа) и уведомление
# Учитывает:
#  • личную TZ пользователя (BotSettings.tz, иначе Europe/Berlin)
#  • маску дней недели dow_mask через mask_to_crон()
#  • eager-load media (selectinload) — без MissingGreenlet/DetachedInstanceError
#  • безопасная публикация через publish_auto(...)
# ------------------------------------------------------------

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import async_session, Job, Account, BotSettings
from app.services.notifications import notify_user, bind_bot
from app.services.schedule_utils import mask_to_cron
from app.services.token_health import periodic_token_health
from app.services.tg_io import get_file_public_url
from app.services.threads_client import ThreadsError, publish_auto

logger = logging.getLogger(__name__)
_scheduler: Optional[AsyncIOScheduler] = None

DEFAULT_TZ = "Europe/Berlin"

# поддержка маркера картинок в тексте
IMG_MARK_RE = re.compile(r"\n\s*\[IMG\]\s+(?P<url>\S+)\s*$", re.IGNORECASE)


def _parse_hhmm(hhmm: str) -> tuple[int, int]:
    if len(hhmm) != 5 or hhmm[2] != ":":
        raise ValueError("bad time format")
    hh, mm = hhmm[:2], hhmm[3:]
    if not (hh.isdigit() and mm.isdigit()):
        raise ValueError("bad time digits")
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("time out of range")
    return h, m


def _split_text_and_image_url(text: str) -> tuple[str, Optional[str]]:
    """Ищем в конце текста маркер вида: \\n[IMG] https://... → (текст_без_маркера, url|None)"""
    if not text:
        return text, None
    m = IMG_MARK_RE.search(text)
    if not m:
        return text, None
    url = m.group("url")
    clean = text[: m.start()].rstrip()
    return clean, url


# ----------------------- ИСПОЛНЕНИЕ JOB -------------------- #

async def _run_job(job_id: int) -> None:
    logger.debug("_run_job: start job_id=%s", job_id)

    # 1) Читаем Job с медиа и Account в одной сессии
    async with async_session() as session:
        res = await session.execute(
            select(Job)
            .options(selectinload(Job.media))
            .where(Job.id == job_id)
        )
        job = res.scalars().first()
        if job is None:
            logger.warning("_run_job: job_id=%s not found", job_id)
            return

        acc = await session.get(Account, job.account_id)
        if acc is None or not acc.access_token:
            await notify_user(job.tg_user_id, f"❌ No token is set. Skipped {job.time_str}")
            logger.warning("_run_job: no token for job_id=%s user=%s", job_id, job.tg_user_id)
            return

        orig_text = job.text or ""
        time_str = job.time_str
        media_items = list(job.media or [])

    # 2) СНАЧАЛА разбираем маркер и ВСЕГДА чистим текст
    text, marker_url = _split_text_and_image_url(orig_text)

    image_urls: list[str] = []
    # НОВОЕ: Флаг для отслеживания ошибок обработки изображений
    image_processing_failed = False

    if marker_url:
        # 2a) Если есть маркер — он имеет приоритет, игнорируем связанные media,
        # чтобы не подтянулась “старая”/не та картинка
        image_urls = [marker_url]
    else:
        # 2b) Маркера нет — используем связанные media из БД
        for m in media_items:
            try:
                if getattr(m, "source", "telegram") == "telegram" and getattr(m, "tg_file_id", None):
                    url = await get_file_public_url(m.tg_file_id)
                    if url:
                        image_urls.append(url)
            except Exception as e:
                logger.warning("media url build failed job_id=%s media_id=%s: %s",
                               job_id, getattr(m, "id", "?"), e)
                # НОВОЕ: Устанавливаем флаг при ошибке
                image_processing_failed = True

    # 3) Публикация
    try:
        logger.debug("publish decision: images=%d marker=%s", len(image_urls), bool(marker_url))
        if image_urls:
            await publish_auto(
                acc.access_token,
                text=text,
                media_type="IMAGE",
                image_urls=image_urls,
            )
        else:
            await publish_auto(
                acc.access_token,
                text=text,
                media_type="TEXT",
            )

        preview = f"{text[:100]}{'…' if len(text) > 100 else ''}"
        nowz = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        
        # --- ИЗМЕНЕНИЕ: Формируем уведомление с возможным предупреждением ---
        # Формируем базовое сообщение
        success_message_lines = [
            f"⏰ {time_str} — published",
            f"🧾 {preview}",
            f"🖼️ images: {len(image_urls)}",
        ]

        # Добавляем предупреждение, если картинки не обработались,
        # но текст успешно опубликован (и картинок в итоге нет)
        if image_processing_failed and not image_urls and media_items:
            success_message_lines.insert(2, "⚠️ (Image failed to process)")

        success_message_lines.append(f"🕒 {nowz}")
        
        # Отправляем уведомление
        await notify_user(
            job.tg_user_id,
            "\n".join(success_message_lines)
        )
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        logger.info("_run_job: posted job_id=%s user=%s time=%s images=%s",
                    job_id, job.tg_user_id, time_str, len(image_urls))

    except ThreadsError as e:
        await notify_user(job.tg_user_id, f"❌ Publish error at {time_str}: {e}")
        logger.warning("_run_job: ThreadsError job_id=%s user=%s: %s", job_id, job.tg_user_id, e)
    except Exception as e:
        await notify_user(job.tg_user_id, f"❌ Unexpected error at {time_str}: {e}")
        logger.exception("_run_job: unexpected error job_id=%s user=%s: %s", job_id, job.tg_user_id, e)


# ----------------------- ЖИЗНЕННЫЙ ЦИКЛ -------------------- #

async def init_scheduler(bot, tz: str = DEFAULT_TZ) -> AsyncIOScheduler:
    global _scheduler
    bind_bot(bot)

    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=ZoneInfo(tz))
        _scheduler.start()
        logger.info("[scheduler] started TZ=%s", tz)

    if not _scheduler.get_job("token_health_job"):
        hours = int(getattr(settings, "TOKEN_HEALTH_INTERVAL_HOURS", 24) or 24)
        _scheduler.add_job(
            periodic_token_health,
            trigger=IntervalTrigger(hours=hours, jitter=60),
            id="token_health_job",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            replace_existing=True,
        )

    await reload_schedule()
    return _scheduler


async def reload_schedule() -> int:
    global _scheduler
    if _scheduler is None:
        logger.warning("reload_schedule: called before init")
        return 0

    keep_ids = {"token_health_job"}
    for job in list(_scheduler.get_jobs()):
        if job.id not in keep_ids:
            _scheduler.remove_job(job.id)

    total = 0

    async with async_session() as session:
        rows = (await session.execute(select(Job))).scalars().all()
        logger.debug("reload_schedule: fetched %s Job row(s)", len(rows))

        for j in rows:
            st = await session.get(BotSettings, j.tg_user_id)
            tz_name = (st.tz if st and getattr(st, "tz", None) else DEFAULT_TZ)
            try:
                user_tz = ZoneInfo(tz_name)
            except Exception:
                logger.warning("reload_schedule: invalid tz=%s for user=%s, fallback=%s",
                               tz_name, j.tg_user_id, DEFAULT_TZ)
                user_tz = ZoneInfo(DEFAULT_TZ)

            try:
                hour, minute = _parse_hhmm(j.time_str)
            except Exception:
                logger.warning("reload_schedule: skip job_id=%s invalid time '%s'", j.id, j.time_str)
                continue

            cron_dow = mask_to_cron(getattr(j, "dow_mask", 127))
            trigger = CronTrigger(hour=hour, minute=minute, timezone=user_tz, day_of_week=cron_dow)

            aps_id = f"post:{j.id}"

            try:
                _scheduler.add_job(
                    _run_job,
                    trigger=trigger,
                    kwargs={"job_id": j.id},
                    id=aps_id,
                    replace_existing=True,
                    misfire_grace_time=600,
                    coalesce=True,
                    max_instances=1,
                )
            except TypeError:
                _scheduler.add_job(
                    (lambda job_id=j.id: asyncio.create_task(_run_job(job_id))),
                    trigger=trigger,
                    id=aps_id,
                    replace_existing=True,
                    misfire_grace_time=600,
                    coalesce=True,
                    max_instances=1,
                )
            except Exception as e:
                logger.exception("reload_schedule: failed add job id=%s: %s", j.id, e)
                continue

            logger.info("reload_schedule: add job id=%s user=%s time=%s tz=%s dow=%s",
                        j.id, j.tg_user_id, j.time_str, tz_name, (cron_dow or "daily"))
            total += 1

    logger.info("reload_schedule: scheduled %s job(s)", total)
    return total


# Backward-compat
async def init_schedule(bot, tz: str = DEFAULT_TZ):
    return await init_scheduler(bot, tz)
