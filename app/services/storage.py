# app/services/storage.py
# ------------------------------------------------------------
# Простая обёртка над внешним хостингом картинок.
# Сейчас реализован IMGBB (https://api.imgbb.com/).
# Если ключ не задан — кидаем NotConfigured, чтобы верхний уровень
# смог мягко деградировать (опубликовать без фото).
# ------------------------------------------------------------

from __future__ import annotations
import base64
from typing import Optional

import httpx

from app.config import settings


class NotConfigured(RuntimeError):
    """Нет конфигурации для загрузки медиа (например, отсутствует API-ключ)."""


async def rehost_image_bytes(data: bytes, filename: str = "image.jpg") -> str:
    """
    Загружает байты изображения на внешний публичный хостинг и возвращает HTTPS-URL.
    Сейчас поддержан только IMGBB (нужен settings.IMGBB_API_KEY).
    """
    api_key: Optional[str] = getattr(settings, "IMGBB_API_KEY", None)
    if not api_key:
        raise NotConfigured("IMGBB_API_KEY is not set")

    # imgbb принимает 'image' как base64
    b64 = base64.b64encode(data).decode("ascii")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.imgbb.com/1/upload",
            data={"key": api_key, "name": filename, "image": b64},
        )
        try:
            j = resp.json()
        except Exception:
            raise RuntimeError(f"imgbb bad response {resp.status_code}: {resp.text[:200]}")

        if resp.status_code >= 400 or not j.get("success"):
            err = j.get("error", {})
            # Вытащим максимально подробно
            raise RuntimeError(f"imgbb upload failed: {err or j}")

        # URL публичной картинки
        url = (j.get("data") or {}).get("url")
        if not url:
            raise RuntimeError(f"imgbb: no url in response: {j}")
        return url
