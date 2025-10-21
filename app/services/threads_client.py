# app/services/threads_client.py
# Совместимый и устойчивый клиент Threads Graph API.
# Создаёт контейнер и публикует его через /me/threads_publish (creation_id=...).
from __future__ import annotations

import json
import logging
import asyncio
from typing import Iterable, Optional, Dict, Any

import httpx

log = logging.getLogger(__name__)

THREADS_BASE = "https://graph.threads.net/v1.0"


# ==== Ошибки ====================================================

class ThreadsError(Exception):
    pass


class ThreadsAPIError(ThreadsError):
    def __init__(self, status: int, url: str, payload: Dict[str, Any], body: str):
        self.status = status
        self.url = url
        self.payload = payload
        self.body = body or ""
        msg = ""
        code = None
        subcode = None
        try:
            data = json.loads(self.body) if self.body else {}
            err = data.get("error") or {}
            msg = err.get("message") or ""
            code = err.get("code")
            subcode = err.get("error_subcode")
        except Exception:
            pass
        super().__init__(
            f"HTTP {status} | url={url} | msg={msg} | code={code}/{subcode} | body={{\"raw\": {self.body!r}}}"
        )


# ==== HTTP helpers ==============================================

def _redact_payload_for_log(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: ("***" if k == "access_token" else v) for k, v in (d or {}).items()}


async def _post_form(url: str, data: Dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as cli:
        return await cli.post(url, data=data)  # x-www-form-urlencoded


async def _post_json(url: str, data: Dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30.0) as cli:
        return await cli.post(url, json=data)


async def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as cli:
        r = await cli.get(url, params=params)
    if r.status_code >= 400:
        raise ThreadsAPIError(r.status_code, url, params, r.text or "")
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text}


async def _post_with_fallback(url: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    1) form-urlencoded
    2) при 500 '' — повтор
    3) снова 500 '' — JSON fallback
    """
    red = _redact_payload_for_log(data)

    r = await _post_form(url, data)
    if r.status_code < 400:
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text}

    body = r.text or ""
    if r.status_code == 500 and body.strip() == "":
        log.warning("Threads 500(empty) on form; retrying… url=%s payload=%s", url, red)
        await asyncio.sleep(0.3)
        r2 = await _post_form(url, data)
        if r2.status_code < 400:
            try:
                return r2.json()
            except Exception:
                return {"_raw": r2.text}
        body2 = r2.text or ""
        if r2.status_code == 500 and body2.strip() == "":
            log.warning("Threads 500(empty) again; JSON fallback… url=%s payload=%s", url, red)
            r3 = await _post_json(url, data)
            if r3.status_code < 400:
                try:
                    return r3.json()
                except Exception:
                    return {"_raw": r3.text}
            raise ThreadsAPIError(r3.status_code, url, data, r3.text or "")
        else:
            raise ThreadsAPIError(r2.status_code, url, data, body2)
    else:
        raise ThreadsAPIError(r.status_code, url, data, body)


# ==== Payload helpers ===========================================

def _ensure_text_payload(access_token: str, text: str) -> Dict[str, Any]:
    """
    Для части раскаток Threads нужен media_type даже для текста.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Text is empty")
    return {
        "access_token": access_token,
        "text": text,
        "media_type": "TEXT",
    }


def _ensure_media_payload(
    access_token: str,
    text: Optional[str],
    media_type: Optional[str],
    media_ids: Optional[Iterable[str]] = None,
    image_urls: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """
    Собираем payload для поста с медиа.
    ФИКСЫ:
      • если media_type не указан, но есть image_urls/media_ids — подставляем 'IMAGE'
      • API в твоём окружении требует image_url (singular) → берём первый URL
      • для media_ids аналогично подстраиваемся на singular media_id (берём первый)
    """
    payload: Dict[str, Any] = {"access_token": access_token}
    if text:
        payload["text"] = text

    # автоопределение типа
    if not media_type and (image_urls or media_ids):
        media_type = "IMAGE"

    if not media_type:
        if "text" in payload:
            return payload
        raise ValueError("media_type is required when no text supplied")

    payload["media_type"] = media_type

    # --- ключевые правки под твой эндпоинт ---
    if image_urls:
        # возьмём первый URL и отправим как image_url
        first_url = next(iter(image_urls))
        payload["image_url"] = str(first_url)
    if media_ids:
        first_id = next(iter(media_ids))
        # отправим как media_id (singular) — чаще всего так ожидается в этом варианте API
        payload["media_id"] = str(first_id)
    # ------------------------------------------

    return payload


# ==== Профиль ===================================================

async def get_profile(access_token: str) -> Dict[str, Any]:
    url = f"{THREADS_BASE}/me"
    params = {"access_token": access_token, "fields": "id,username,name"}
    return await _get_json(url, params)


# ==== Публикация ================================================

async def _publish_container(access_token: str, container_id: str) -> Dict[str, Any]:
    """
    Корректная публикация: POST /me/threads_publish  creation_id=<id>
    """
    url = f"{THREADS_BASE}/me/threads_publish"
    data = {"access_token": access_token, "creation_id": container_id}
    r = await _post_form(url, data)
    if r.status_code >= 400:
        raise ThreadsAPIError(r.status_code, url, data, r.text or "")
    try:
        return r.json()
    except Exception:
        return {"_raw": r.text}


async def _create_and_publish(url: str, access_token: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    1) создаём контейнер (POST /me/threads)
    2) публикуем его (POST /me/threads_publish)
    """
    created = await _post_with_fallback(url, data)
    container_id = str(created.get("id") or "").strip()
    if not container_id:
        return created
    published = await _publish_container(access_token, container_id)
    return {"id": container_id, "published": published}


async def post_thread_text(access_token: str, text: str) -> Dict[str, Any]:
    url = f"{THREADS_BASE}/me/threads"
    data = _ensure_text_payload(access_token, text)
    return await _create_and_publish(url, access_token, data)


async def post_thread(
    access_token: str,
    *,
    text: Optional[str] = None,
    media_type: Optional[str] = None,           # 'TEXT' | 'IMAGE' | 'VIDEO'
    media_ids: Optional[Iterable[str]] = None,
    image_urls: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    url = f"{THREADS_BASE}/me/threads"
    if media_type or media_ids or image_urls:
        data = _ensure_media_payload(access_token, text, media_type, media_ids, image_urls)
    else:
        data = _ensure_text_payload(access_token, text or "")
    return await _create_and_publish(url, access_token, data)


# ==== Совместимость для планировщика ============================

async def publish_auto(
    access_token: str,
    *,
    text: Optional[str] = None,
    media_type: Optional[str] = None,
    media_ids: Optional[Iterable[str]] = None,
    image_urls: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    return await post_thread(
        access_token,
        text=text,
        media_type=media_type,
        media_ids=media_ids,
        image_urls=image_urls,
    )


# ==== Алиасы ====================================================

create_thread = post_thread
publish_thread = post_thread
publish_text_thread = post_thread_text
get_me = get_profile

__all__ = [
    "ThreadsError",
    "ThreadsAPIError",
    "get_profile",
    "post_thread_text",
    "post_thread",
    "publish_auto",
    "create_thread",
    "publish_thread",
    "publish_text_thread",
    "get_me",
]
