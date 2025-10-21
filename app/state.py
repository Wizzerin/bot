from app.config import settings

# Глобальное (в рамках процесса) хранилище текущего токена Threads.
# Стартовое значение берём из .env, далее /set_token будет его менять.
current_threads_token: str = settings.THREADS_TOKEN or ""