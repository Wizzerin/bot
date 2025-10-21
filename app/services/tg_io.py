# app/services/tg_io.py
# ------------------------------------------------------------
# Привязка Bot и выдача публичных URL для медиа.
# build_public_url(file_id):
#   1) скачивает файл из Telegram
#   2) пытается залить на Telegraph (несколько доменов)
#   3) если не вышло — заливает на catbox.moe (анонимно)
#
# Совместимые алиасы сохранены:
#   file_public_url / get_public_url / get_file_public_url
# ------------------------------------------------------------

from __future__ import annotations

import os
import mimetypes
from typing import Optional, Tuple

from aiogram import Bot
import httpx

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
    file_path = tg_file.file_path  # например photos/file_10.jpg
    filename = os.path.basename(file_path) or "file"
    url = f"https://api.telegram.org/file/bot{_bot.token}/{file_path}"

    async with httpx.AsyncClient(timeout=60.0) as cli:
        resp = await cli.get(url)
        resp.raise_for_status()
        data = resp.content

    ct, _ = mimetypes.guess_type(filename)
    if not ct:
        # попытаемся угадать по сигнатурам позже при необходимости
        ct = "application/octet-stream"
    return data, filename, ct


async def _upload_to_telegraph_like(host: str, data: bytes, filename: str, content_type: str) -> str:
    """
    Грузим файл на один из Telegraph-хостов:
      host in {"https://telegra.ph", "https://te.legra.ph", "https://graph.org"}
    Возвращаем публичный https-URL.
    """
    url = f"{host}/upload"
    files = {"file": (filename, data, content_type)}
    async with httpx.AsyncClient(timeout=60.0) as cli:
        r = await cli.post(url, files=files)
        r.raise_for_status()
        js = r.json()
        # формат ответа: [{"src": "/file/xxxxxxxxx.jpg"}]
        if not isinstance(js, list) or not js or "src" not in js[0]:
            raise RuntimeError(f"telegraph upload unexpected response: {js!r}")
        src = js[0]["src"]
        if not isinstance(src, str) or not src.startswith("/"):
            raise RuntimeError(f"telegraph src invalid: {src!r}")
        return f"{host}{src}"


async def _upload_to_catbox(data: bytes, filename: str, content_type: str) -> str:
    """
    Fallback-хостинг: catbox.moe (анонимно).
    Возвращает публичный URL в виде простого текста.
    API: POST https://catbox.moe/user/api.php
      data: { reqtype: 'fileupload' }
      files: { fileToUpload: <file> }
    """
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
    Возвращает ПУБЛИЧНЫЙ URL для файла Telegram, годный для Threads API.
    Порядок:
      1) telegra.ph
      2) te.legra.ph
      3) graph.org
      4) catbox.moe (fallback, принимает большие файлы)
    """
    data, filename, ct = await _download_tg_file_bytes(file_id)

    # Пробуем Telegraph-провайдеров по очереди
    errors = []

    for host in ("https://telegra.ph", "https://te.legra.ph", "https://graph.org"):
        try:
            return await _upload_to_telegraph_like(host, data, filename, ct)
        except Exception as e:
            errors.append(f"{host}: {e}")

    # Fallback на catbox.moe (подходит под ограничения Threads: публичный CDN-URL)
    try:
        return await _upload_to_catbox(data, filename, ct)
    except Exception as e:
        errors.append(f"catbox.moe: {e}")

    # Если все варианты упали — бросаем подробную ошибку (логи наверху поймают)
    raise RuntimeError("All re-host attempts failed: " + " | ".join(errors))


# ---------- Алиасы для обратной совместимости ----------

async def file_public_url(file_id: str) -> str:
    return await build_public_url(file_id)


async def get_public_url(file_id: str) -> str:
    return await build_public_url(file_id)


async def get_file_public_url(file_id: str) -> str:
    return await build_public_url(file_id)
