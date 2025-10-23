# app/services/ai_assistant.py
# ------------------------------------------------------------
# (ИЗМЕНЕНИЕ) Добавлена функция suggest_hashtags.
# ------------------------------------------------------------

import logging
import httpx
import json
import re # Для очистки хэштегов

log = logging.getLogger(__name__)

async def _call_gemini(system_prompt: str, user_query: str, max_tokens: int = 1024) -> str:
    """Внутренняя функция для вызова Gemini API."""
    api_key = "AIzaSyCwIJKqoCsjy53L3NB7S5Ye_ndpxlabk34" # Замени на свой ключ
    if not api_key:
         log.error("Gemini API key is not set in ai_assistant.py")
         raise ValueError("Gemini API key is not configured.") # Лучше выбросить исключение

    api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": user_query}]}],
        "systemInstruction": { "parts": [{"text": system_prompt}] },
        "generationConfig": { "temperature": 0.7, "maxOutputTokens": max_tokens }
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(api_url, json=payload, headers={'Content-Type': 'application/json'})
            response.raise_for_status()
            result = response.json()

            candidates = result.get("candidates")
            if not candidates:
                prompt_feedback = result.get("promptFeedback")
                if prompt_feedback and prompt_feedback.get("blockReason"):
                    block_reason = prompt_feedback.get("blockReason")
                    log.warning("Gemini request blocked. Reason: %s. Prompt: %s", block_reason, user_query)
                    raise ValueError(f"AI request blocked due to safety restrictions ({block_reason}).")
                else:
                    log.error("Gemini response missing 'candidates'. Full response: %s", result)
                    raise ValueError("AI returned an unexpected response (no candidates).")

            candidate = candidates[0]
            content = candidate.get("content")
            if not content:
                 finish_reason = candidate.get("finishReason")
                 if finish_reason and finish_reason != "STOP":
                      if finish_reason == "MAX_TOKENS":
                           log.warning("Gemini generation stopped due to MAX_TOKENS limit.")
                           raise ValueError("AI response was too long and got cut off.")
                      log.warning("Gemini generation finished unexpectedly. Reason: %s. Candidate: %s", finish_reason, candidate)
                      raise ValueError(f"AI generation stopped unexpectedly ({finish_reason}).")
                 else:
                      log.error("Gemini response missing 'content' in candidate. Full response: %s", result)
                      raise ValueError("AI returned an unexpected response structure (no content).")

            parts = content.get("parts")
            if not parts:
                 log.error("Gemini response missing 'parts' in content. Full response: %s", result)
                 raise ValueError("AI did not generate any text content.")

            generated_text = parts[0].get("text", "").strip()
            if not generated_text:
                 log.warning("Gemini generated an empty text response. Full response: %s", result)
                 raise ValueError("AI generated an empty response.")

            return generated_text

    except httpx.HTTPStatusError as e:
        log.error("Gemini API HTTP error: %s - %s", e.response.status_code, e.response.text)
        if e.response.status_code == 403:
             raise ValueError("AI service permission issue (check API Key?).")
        elif e.response.status_code == 429:
             raise ValueError("AI service is overloaded. Please try again later.")
        else:
            raise ValueError(f"Error communicating with the AI ({e.response.status_code}).")
    except Exception as e:
        log.exception("Error calling Gemini API: %s", e)
        # Перебрасываем исключение, чтобы вызывающий код мог его обработать
        raise ValueError("An unexpected error occurred while contacting the AI.")


async def generate_reply_with_gemini(original_comment: str, account_name: str) -> str:
    """Generates a reply draft using Gemini API."""
    system_prompt = (
        f"You are a friendly and helpful SMM assistant for the Threads account named '{account_name}'. "
        f"Your tone should be conversational and engaging, not overly formal or robotic. "
        f"You received a comment from a user. Write a concise, polite, and relevant draft reply to this comment. "
        f"Keep the reply relatively short (1-3 sentences)."
    )
    user_query = f"Draft a reply to this comment: \"{original_comment}\""
    try:
        reply = await _call_gemini(system_prompt, user_query, max_tokens=256) # Уменьшил макс токены для ответа
        log.info("Gemini generated reply for comment: '%s'", original_comment)
        return reply
    except ValueError as e:
        # Возвращаем сообщение об ошибке пользователю
        return f"Sorry, I couldn't generate a reply: {e}"


# --- (НОВАЯ ФУНКЦИЯ) ---
async def suggest_hashtags(post_text: str) -> str:
    """Suggests relevant hashtags for a post using Gemini API."""
    if not post_text:
        return "" # Нечего предлагать для пустого текста

    system_prompt = (
        "You are an expert SMM assistant specializing in Threads. "
        "Your task is to suggest relevant and effective hashtags for a given post text. "
        "Provide a list of 5-10 hashtags, separated by spaces. "
        "Include a mix of popular and niche hashtags relevant to the content. "
        "Ensure each hashtag starts with '#' and contains only letters, numbers, and underscores."
        "Output ONLY the hashtags themselves, separated by spaces." # Важно для парсинга
    )
    user_query = f"Suggest hashtags for this Threads post:\n\n\"{post_text}\""

    try:
        # Используем больше токенов, так как анализ текста может быть сложнее
        raw_hashtags = await _call_gemini(system_prompt, user_query, max_tokens=512)

        # Очистка и форматирование результата
        # Убираем лишние символы, оставляем только валидные хэштеги
        hashtags = re.findall(r'#[\w_]+', raw_hashtags)
        cleaned_hashtags = " ".join(hashtags)

        log.info("Gemini suggested hashtags for text: '%s...'", post_text[:50])
        return cleaned_hashtags
    except ValueError as e:
        # В случае ошибки возвращаем пустую строку или сообщение об ошибке
        log.warning("Failed to suggest hashtags: %s", e)
        # Можно вернуть e, чтобы показать пользователю, или просто ""
        return "" # Возвращаем пустую строку, чтобы не ломать копирование

