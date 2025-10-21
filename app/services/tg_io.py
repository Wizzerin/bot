# app/services/tg_io.py
# ------------------------------------------------------------
# (ИЗМЕНЕНО) Привязка Bot и выдача публичных URL для медиа.
# Основной хостинг теперь - imgbb.com, остальные - резервные.
# ------------------------------------------------------------

from __future__ import annotations

import os
import base64
import mimetypes
from typing import Optional, Tuple

from aiogram import Bot
import httpx

from app.config import settings # Для доступа к IMGBB_API_KEY

_bot: Optional[Bot] = None


def bind_bot(bot: Bot) -> None:
    """Связываем текущий Bot, чтобы уметь получать file_path и скачивать файл."""
    global _bot
    _bot = bot


async def _download_tg_file_bytes(file_id: str) -> Tuple[bytes, str, str]:
    """
    Скачиваем файл из Telegram по file_id и возвращаем (bytes, filename, content_type).
    """
    if _bot is None:
        raise RuntimeError("tg_io: bot is not bound")

    tg_file = await _bot.get_file(file_id)
    file_path = tg_file.file_path
    filename = os.path.basename(file_path) or "file"
    url = f"https://api.telegram.org/file/bot{_bot.token}/{file_path}"

    async with httpx.AsyncClient(timeout=60.0) as cli:
        resp = await cli.get(url)
        resp.raise_for_status()
        data = resp.content

    ct, _ = mimetypes.guess_type(filename)
    if not ct:
        ct = "application/octet-stream"
    return data, filename, ct


async def _upload_to_imgbb(data: bytes, filename: str) -> str:
    """(НОВЫЙ) Загружает байты на imgbb.com и возвращает URL."""
    api_key = settings.IMGBB_API_KEY
    if not api_key:
        raise RuntimeError("IMGBB_API_KEY is not configured in .env")

    b64_image = base64.b64encode(data).decode("ascii")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key, "name": filename, "image": b64_image},
        )
        resp.raise_for_status()
        j = resp.json()

        if not j.get("success"):
            err = j.get("error", {})
            raise RuntimeError(f"imgbb upload failed: {err or j}")

        url = (j.get("data") or {}).get("url")
        if not url:
            raise RuntimeError(f"imgbb: no url in response: {j}")
        return url


async def _upload_to_telegraph_like(host: str, data: bytes, filename: str, content_type: str) -> str:
    """Грузим файл на один из Telegraph-хостов (резервный метод)."""
    url = f"{host}/upload"
    files = {"file": (filename, data, content_type)}
    async with httpx.AsyncClient(timeout=60.0) as cli:
        r = await cli.post(url, files=files)
        r.raise_for_status()
        js = r.json()
        if not isinstance(js, list) or not js or "src" not in js[0]:
            raise RuntimeError(f"telegraph upload unexpected response: {js!r}")
        src = js[0]["src"]
        if not isinstance(src, str) or not src.startswith("/"):
            raise RuntimeError(f"telegraph src invalid: {src!r}")
        return f"{host}{src}"


async def _upload_to_catbox(data: bytes, filename: str, content_type: str) -> str:
    """Резервный хостинг: catbox.moe."""
    api = "https://catbox.moe/user/api.php"
    form = {"reqtype": "fileupload"}
    files = {"fileToUpload": (filename, data, content_type)}
    async with httpx.AsyncClient(timeout=120.0) as cli:
        r = await cli.post(api, data=form, files=files)
        r.raise_for_status()
        url = r.text.strip()
        if not (url.startswith("https://") or url.startswith("http://")):
            raise RuntimeError(f"catbox upload unexpected response: {url!r}")
        return url


async def build_public_url(file_id: str) -> str:
    """
    (ИЗМЕНЕНО) Возвращает ПУБЛИЧНЫЙ URL для файла Telegram.
    Порядок:
      1) imgbb.com (основной)
      2) telegra.ph (резервный)
      3) te.legra.ph (резервный)
      4) graph.org (резервный)
      5) catbox.moe (последний резервный)
    """
    data, filename, ct = await _download_tg_file_bytes(file_id)
    errors = []

    # 1. Пробуем imgbb
    if settings.IMGBB_API_KEY:
        try:
            return await _upload_to_imgbb(data, filename)
        except Exception as e:
            errors.append(f"imgbb.com: {e}")

    # 2. Пробуем Telegraph-провайдеров
    for host in ("https://telegra.ph", "https://te.legra.ph", "https://graph.org"):
        try:
            return await _upload_to_telegraph_like(host, data, filename, ct)
        except Exception as e:
            errors.append(f"{host}: {e}")

    # 3. Последний резервный вариант - catbox.moe
    try:
        return await _upload_to_catbox(data, filename, ct)
    except Exception as e:
        errors.append(f"catbox.moe: {e}")

    raise RuntimeError("All re-host attempts failed: " + " | ".join(errors))


# ---------- Алиасы для обратной совместимости ----------

async def file_public_url(file_id: str) -> str:
    return await build_public_url(file_id)

async def get_public_url(file_id: str) -> str:
    return await build_public_url(file_id)

async def get_file_public_url(file_id: str) -> str:
    return await build_public_url(file_id)
