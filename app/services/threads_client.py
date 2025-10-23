# app/services/threads_client.py
# ------------------------------------------------------------
# (ИЗМЕНЕНИЕ) Добавлена функция get_user_media для импорта постов.
# ------------------------------------------------------------
from __future__ import annotations

import json
import logging
import asyncio
from typing import Iterable, Optional, Dict, Any, List

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
        self.message = ""
        self.code = None
        self.subcode = None
        try:
            data = json.loads(self.body) if self.body else {}
            err = data.get("error") or {}
            self.message = err.get("message") or ""
            self.code = err.get("code")
            self.subcode = err.get("error_subcode")
        except Exception:
            pass
        super().__init__(
            f"HTTP {status} | url={url} | msg={self.message} | code={self.code}/{self.subcode} | body={{\"raw\": {self.body!r}}}"
        )


# ==== HTTP helpers ==============================================

def _redact_payload_for_log(d: Dict[str, Any]) -> Dict[str, Any]:
    # Redacts sensitive info like access_token for logging
    return {k: ("***" if k == "access_token" else v) for k, v in (d or {}).items()}


async def _post_form(url: str, data: Dict[str, Any]) -> httpx.Response:
    # Sends POST request with form-urlencoded data
    async with httpx.AsyncClient(timeout=30.0) as cli:
        return await cli.post(url, data=data)


async def _post_json(url: str, data: Dict[str, Any]) -> httpx.Response:
    # Sends POST request with JSON data
    async with httpx.AsyncClient(timeout=30.0) as cli:
        return await cli.post(url, json=data)


async def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    # Sends GET request and parses JSON response
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
    Handles Threads API quirks: tries form-urlencoded first, retries on empty 500,
    then falls back to JSON payload if needed.
    """
    red = _redact_payload_for_log(data)
    r = await _post_form(url, data)
    if r.status_code < 400:
        try: return r.json()
        except Exception: return {"_raw": r.text}

    body = r.text or ""
    # Retry logic for specific empty 500 errors
    if r.status_code == 500 and body.strip() == "":
        log.warning("Threads 500(empty) on form; retrying… url=%s payload=%s", url, red)
        await asyncio.sleep(0.3)
        r2 = await _post_form(url, data)
        if r2.status_code < 400:
            try: return r2.json()
            except Exception: return {"_raw": r2.text}
        body2 = r2.text or ""
        # Fallback to JSON if retry also fails with empty 500
        if r2.status_code == 500 and body2.strip() == "":
            log.warning("Threads 500(empty) again; JSON fallback… url=%s payload=%s", url, red)
            r3 = await _post_json(url, data)
            if r3.status_code < 400:
                try: return r3.json()
                except Exception: return {"_raw": r3.text}
            raise ThreadsAPIError(r3.status_code, url, data, r3.text or "")
        else:
            raise ThreadsAPIError(r2.status_code, url, data, body2)
    else:
        raise ThreadsAPIError(r.status_code, url, data, body)


# ==== Payload helpers ===========================================

def _prepare_payload(
    access_token: str,
    *,
    text: Optional[str] = None,
    media_type: Optional[str] = None,
    image_urls: Optional[Iterable[str]] = None,
    is_carousel_item: bool = False,
    children: Optional[list[str]] = None,
    reply_to_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Prepares payload for creating a media container (text, image, carousel, or reply)."""
    payload: Dict[str, Any] = {"access_token": access_token}
    if text: payload["text"] = text

    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
        if not media_type:
             media_type = "TEXT"
    
    if not media_type:
        if children: media_type = "CAROUSEL"
        elif image_urls: media_type = "IMAGE"
        elif text: media_type = "TEXT"
        else: raise ValueError("Cannot determine media type. Text, image_urls, or children required.")
    payload["media_type"] = media_type

    if media_type == "IMAGE":
        if not image_urls: raise ValueError("image_urls required for IMAGE type.")
        payload["image_url"] = str(next(iter(image_urls)))
        if is_carousel_item: payload["is_carousel_item"] = "true"
    elif media_type == "CAROUSEL":
        if not children: raise ValueError("children container IDs required for CAROUSEL type.")
        payload["children"] = ",".join(map(str, children))
    elif media_type == "TEXT":
        if not text and not reply_to_id: raise ValueError("text required for TEXT type unless it's a reply.")

    return payload


# ==== Профиль ===================================================

async def get_profile(access_token: str) -> Dict[str, Any]:
    """Gets basic profile info (id, username)."""
    url = f"{THREADS_BASE}/me"
    params = {"access_token": access_token, "fields": "id,username,name"}
    return await _get_json(url, params)


# ==== Статистика поста =========================================

async def get_post_metrics(access_token: str, post_id: str) -> Dict[str, Any]:
    """Gets metrics (likes, replies) for a post. Handles partial unavailability."""
    url = f"{THREADS_BASE}/{post_id}"
    params = {"access_token": access_token, "fields": "id,like_count,replies_count"}
    log.debug("Requesting metrics for post %s", post_id)
    metrics = {"id": post_id, "likes": "N/A", "replies": "N/A"}

    try:
        data = await _get_json(url, params)
        log.debug("Metrics received: %s", data)
        if "like_count" in data: metrics["likes"] = data["like_count"]
        if "replies_count" in data: metrics["replies"] = data["replies_count"]
        return metrics
    except ThreadsAPIError as e:
        error_msg = getattr(e, 'message', '')
        # Fallback logic if like_count is missing (common in dev mode)
        if e.status == 500 and "nonexisting field (like_count)" in error_msg:
            log.warning("Metrics for post %s partially unavailable: like_count missing. API msg: %s", post_id, error_msg)
            try:
                params_fallback = {"access_token": access_token, "fields": "id,replies_count"}
                data_fallback = await _get_json(url, params_fallback)
                if "replies_count" in data_fallback: metrics["replies"] = data_fallback["replies_count"]
                log.debug("Fallback metrics received: %s", data_fallback)
                return metrics
            except Exception as fallback_e:
                log.warning("Fallback metrics request (replies only) also failed for post %s: %s", post_id, fallback_e)
                return metrics
        # Fallback logic if replies_count is missing
        elif e.status == 500 and "nonexisting field (replies_count)" in error_msg:
             log.warning("Metrics for post %s partially unavailable: replies_count missing. API msg: %s", post_id, error_msg)
             try:
                 params_fallback = {"access_token": access_token, "fields": "id,like_count"}
                 data_fallback = await _get_json(url, params_fallback)
                 if "like_count" in data_fallback: metrics["likes"] = data_fallback["like_count"]
                 log.debug("Fallback metrics received: %s", data_fallback)
                 return metrics
             except Exception as fallback_e:
                 log.warning("Fallback metrics request (likes only) also failed for post %s: %s", post_id, fallback_e)
                 return metrics
        else:
            log.warning("Failed to get metrics for post %s: %s", post_id, e)
            raise
    except Exception as e_unexp:
        log.exception("Unexpected error getting metrics for post %s: %s", post_id, e_unexp)
        raise ThreadsError(f"Unexpected error getting metrics: {e_unexp}") from e_unexp


# ==== Комментарии к посту =========================================

async def get_post_comments(access_token: str, post_id: str, limit: int = 25, after: Optional[str] = None) -> Dict[str, Any]:
    """
    Gets replies (comments) for a post. Supports pagination via 'after' cursor.
    Returns dict with 'data' (list of comments) and 'paging' info.
    """
    url = f"{THREADS_BASE}/{post_id}/replies"
    params = {
        "access_token": access_token,
        "fields": "id,text,timestamp,username", # Fields needed for display and reply
        "limit": limit
    }
    if after:
        params["after"] = after

    log.debug("Requesting comments for post %s (limit=%d, after=%s)", post_id, limit, after)
    try:
        data = await _get_json(url, params)
        log.debug("Comments received: %d comments", len(data.get("data", [])))
        if "data" not in data:
            data["data"] = []
        return data
    except ThreadsAPIError as e:
        log.warning("Failed to get comments for post %s: %s", post_id, e)
        if e.status == 500 and "nonexisting field (replies)" in getattr(e, 'message', ''):
             log.warning("API reports 'replies' field is non-existent (likely due to dev mode or post type). Returning empty list.")
             return {"data": [], "paging": {}} # Return empty data gracefully
        raise
    except Exception as e_unexp:
        log.exception("Unexpected error getting comments for post %s: %s", post_id, e_unexp)
        raise ThreadsError(f"Unexpected error getting comments: {e_unexp}") from e_unexp


# ==== (НОВАЯ ФУНКЦИЯ) Посты пользователя ==========================

async def get_user_media(access_token: str, limit: int = 10) -> Dict[str, Any]:
    """
    Gets the user's own most recent media (posts).
    Used for importing manually created posts.
    """
    url = f"{THREADS_BASE}/me/threads"
    params = {
        "access_token": access_token,
        # media_type (IMAGE, VIDEO, TEXT, CAROUSEL)
        # media_product_type (THREADS) - to filter out Insta posts
        # permalink - for user reference, though not used by API
        # timestamp - for sorting and display
        "fields": "id,text,timestamp,media_type,media_product_type,permalink",
        "limit": limit
    }
    log.debug("Requesting user's media (limit=%d)", limit)
    try:
        data = await _get_json(url, params)
        log.debug("User media received: %d posts", len(data.get("data", [])))
        if "data" not in data:
            data["data"] = []
        # Filter out non-Threads posts if any
        data["data"] = [p for p in data["data"] if p.get("media_product_type") == "THREADS"]
        return data
    except ThreadsAPIError as e:
        log.warning("Failed to get user media: %s", e)
        # Handle "needs threads_publishing" error gracefully
        if e.code == 10 and "threads_publishing" in getattr(e, 'message', ''):
             log.error("Failed to get user media: App is missing 'threads_publishing' permission.")
             raise ThreadsError("The app is missing 'threads_publishing' permission.") from e
        raise
    except Exception as e_unexp:
        log.exception("Unexpected error getting user media: %s", e_unexp)
        raise ThreadsError(f"Unexpected error getting user media: {e_unexp}") from e_unexp

# ================================================================


# ==== Публикация ================================================

async def _publish_container(access_token: str, container_id: str) -> Dict[str, Any]:
    """Publishes a created container."""
    url = f"{THREADS_BASE}/me/threads_publish"
    data = {"access_token": access_token, "creation_id": container_id}
    log.debug("Publishing container %s...", container_id)
    r = await _post_form(url, data)
    if r.status_code >= 400:
        raise ThreadsAPIError(r.status_code, url, data, r.text or "")
    try:
        result = r.json()
        log.debug("Container %s published response: %s", container_id, result)
        return result
    except Exception:
        log.warning("Container %s published but response was not JSON: %s", container_id, r.text)
        return {"_raw": r.text}

async def _create_media_container(access_token: str, payload: Dict[str, Any]) -> str:
    """Creates a media container (text, image, carousel item, or reply) and returns its ID."""
    url = f"{THREADS_BASE}/me/threads"
    log.debug("Creating media container with payload: %s", _redact_payload_for_log(payload))
    result = await _post_with_fallback(url, payload)
    container_id = str(result.get("id") or "").strip()
    if not container_id:
        log.error("Failed to create media container. API response: %s", result)
        raise ThreadsError(f"Failed to create media container. Response: {result}")
    log.debug("Media container created: %s", container_id)
    return container_id

async def post_thread(
    access_token: str,
    *,
    text: Optional[str] = None,
    image_urls: Optional[Iterable[str]] = None,
    reply_to_id: Optional[str] = None, # ID of the comment being replied to
) -> Dict[str, Any]:
    """
    Publishes content to Threads. Handles text, single image, carousel (up to 10 images),
    and replies to specific comments.
    """
    images = list(image_urls or [])
    num_images = len(images)
    container_id = None
    media_type_for_payload = None

    try:
        if reply_to_id:
            log.info("Preparing reply to comment %s", reply_to_id)
            media_type_for_payload = "TEXT"
            payload = _prepare_payload(access_token, text=text, reply_to_id=reply_to_id, media_type=media_type_for_payload)
            container_id = await _create_media_container(access_token, payload)

        elif text and not images:
            log.info("Preparing text-only post.")
            media_type_for_payload = "TEXT"
            payload = _prepare_payload(access_token, text=text, media_type=media_type_for_payload)
            container_id = await _create_media_container(access_token, payload)

        elif num_images == 1:
            log.info("Preparing single image post.")
            media_type_for_payload = "IMAGE"
            payload = _prepare_payload(access_token, text=text, media_type=media_type_for_payload, image_urls=images)
            container_id = await _create_media_container(access_token, payload)

        elif 1 < num_images <= 10:
            log.info("Preparing carousel post with %d images.", num_images)
            child_ids = []
            for i, img_url in enumerate(images):
                log.debug("Creating carousel item %d/%d", i + 1, num_images)
                item_payload = _prepare_payload(access_token, media_type="IMAGE", image_urls=[img_url], is_carousel_item=True)
                child_id = await _create_media_container(access_token, item_payload)
                child_ids.append(child_id)
                await asyncio.sleep(0.5)

            log.debug("Creating main carousel container with children: %s", child_ids)
            media_type_for_payload = "CAROUSEL"
            carousel_payload = _prepare_payload(access_token, text=text, media_type=media_type_for_payload, children=child_ids)
            container_id = await _create_media_container(access_token, carousel_payload)

        else:
            if num_images > 10: raise ValueError("Cannot publish more than 10 images in a carousel.")
            elif not text: raise ValueError("Post must contain text or at least one image.")
            else: raise ValueError("Invalid content combination for post.")

        if not container_id:
            raise ThreadsError("Failed to create any container.")

        log.info("Publishing container %s...", container_id)
        published_result = await _publish_container(access_token, container_id)
        log.info("Container %s published successfully.", container_id)
        
        final_post_id = published_result.get("id") or container_id
        return {"id": final_post_id, "published": published_result}

    except Exception as e:
        log.exception("Error during post_thread execution: %s", e)
        if isinstance(e, ThreadsError):
            raise
        raise ThreadsError(f"Failed to post thread: {e}") from e


# ==== Совместимость и Алиасы ====================================

async def publish_auto(
    access_token: str,
    *,
    text: Optional[str] = None,
    media_type: Optional[str] = None, # Param ignored
    media_ids: Optional[Iterable[str]] = None, # Param ignored
    image_urls: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Compatibility wrapper. Calls the main post_thread function."""
    log.debug("publish_auto called (text=%s, images=%s)", bool(text), bool(image_urls))
    return await post_thread(access_token, text=text, image_urls=image_urls)

async def post_thread_text(access_token: str, text: str) -> Dict[str, Any]:
    """Alias for posting text-only threads."""
    return await post_thread(access_token, text=text)

async def post_reply(access_token: str, text: str, reply_to_id: str) -> Dict[str, Any]:
     """Alias for posting a reply to a specific comment/post ID."""
     return await post_thread(access_token, text=text, reply_to_id=reply_to_id)


create_thread = post_thread
publish_thread = post_thread
publish_text_thread = post_thread_text
get_me = get_profile

__all__ = [
    "ThreadsError", "ThreadsAPIError",
    "get_profile", "get_post_metrics", "get_post_comments", "get_user_media", # <-- Добавлено
    "post_thread_text", "post_thread", "post_reply",
    "publish_auto",
    "create_thread", "publish_thread", "publish_text_thread", "get_me",
]

