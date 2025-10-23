# app/keyboards.py
# ------------------------------------------------------------
# (ИЗМЕНЕНИЕ) Исправлена ошибка 'str' object has no attribute 'strftime' в archive_dates_kb.
# Все инлайн-клавиатуры бота.
# ------------------------------------------------------------

from __future__ import annotations

import locale # Для форматирования дат
from typing import Iterable, List, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton # Добавлен KeyboardButton
from datetime import datetime, date # Добавлено date

from app.database.models import Job, Account, PublishedPost, Draft # Добавлен Draft

# Попробуем установить локаль для названий месяцев
try:
    # Установим en_US, чтобы месяцы были на английском
    locale.setlocale(locale.LC_TIME, 'en_US.UTF-8')
except locale.Error:
    try:
        # Fallback для некоторых систем
        locale.setlocale(locale.LC_TIME, 'en_US')
    except locale.Error:
        print("Warning: Could not set locale to en_US for month names.")
        pass # Игнорируем ошибку, если локаль не установлена

# =========================
#   ГЛАВНОЕ МЕНЮ И НАСТРОЙКИ
# =========================

def main_menu_kb() -> InlineKeyboardMarkup:
    """Главное меню (инлайн-версия)."""
    rows = [
        [InlineKeyboardButton(text="📝 Post now", callback_data="post_now")],
        [InlineKeyboardButton(text="⏱ Schedule", callback_data="sched_menu")],
        [InlineKeyboardButton(text="📄 Drafts", callback_data="drafts_menu")], # Добавлена кнопка Drafts
        [InlineKeyboardButton(text="🔑 Accounts", callback_data="tok_accounts")],
        [InlineKeyboardButton(text="⚙️ Settings", callback_data="settings_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu_kb() -> InlineKeyboardMarkup:
    """Меню настроек."""
    rows = [
        [InlineKeyboardButton(text="🔔 Notifications", callback_data="notify_menu")],
        [InlineKeyboardButton(text="🗄️ Archive", callback_data="archive_list:0")], # Добавлена кнопка Archive
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="help_show")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
#   АККАУНТЫ И ТОКЕНЫ
# =========================

def accounts_menu_kb(accounts: list[Account]) -> InlineKeyboardMarkup:
    """Меню управления аккаунтами. Показывает список аккаунтов или только кнопку "Set"."""
    rows = []
    for acc in accounts:
        default_marker = " ⭐️" if acc.is_default else ""
        label = acc.title or getattr(acc, 'threads_username', f"Account {acc.id}")
        rows.append([
            InlineKeyboardButton(text=f"{label}{default_marker}", callback_data=f"acc_view:{acc.id}")
        ])

    rows.append([InlineKeyboardButton(text="🔑 Set token", callback_data="tok_set")])
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_actions_kb(acc_id: int) -> InlineKeyboardMarkup:
    """Кнопки действий для конкретного аккаунта."""
    rows = [
        [
            InlineKeyboardButton(text="✏️ Rename", callback_data=f"acc_rename:{acc_id}"),
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"acc_delete:{acc_id}"),
        ],
        [InlineKeyboardButton(text="⭐️ Set as default", callback_data=f"acc_set_default:{acc_id}")],
        [InlineKeyboardButton(text="⬅️ Back to Accounts", callback_data="tok_accounts")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def account_delete_confirm_kb(acc_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления аккаунта."""
    rows = [
        [InlineKeyboardButton(text="✅ Yes, delete", callback_data=f"acc_delete_confirm:{acc_id}")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data=f"acc_view:{acc_id}")], # Используем переданный acc_id
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# =========================
#   УВЕДОМЛЕНИЯ И ЧАСОВОЙ ПОЯС
# =========================

def notify_menu() -> InlineKeyboardMarkup:
    """Меню уведомлений."""
    rows = [
        [InlineKeyboardButton(text="📍 Send reports here", callback_data="notify_here")],
        [InlineKeyboardButton(text="📊 Status", callback_data="notify_status")],
        [InlineKeyboardButton(text="🌍 Time Zone", callback_data="tz_menu")], # Ссылка на tz_menu
        [InlineKeyboardButton(text="🧪 Test", callback_data="notify_test")],
        [InlineKeyboardButton(text="🔕 Off", callback_data="notify_off")],
        [InlineKeyboardButton(text="⬅️ Back to Settings", callback_data="settings_menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- (ВОССТАНОВЛЕНА ФУНКЦИЯ tz_menu) ---
def tz_menu(current_tz: str) -> InlineKeyboardMarkup:
    """Меню настройки часового пояса."""
    rows = [
        [InlineKeyboardButton(text=f"Current: {current_tz}", callback_data="tz_enter")],
        [
            InlineKeyboardButton(text="Europe/Kyiv", callback_data="tz_set:Europe/Kyiv"),
            InlineKeyboardButton(text="Europe/Berlin", callback_data="tz_set:Europe/Berlin"),
        ],
        [InlineKeyboardButton(text="Enter manually...", callback_data="tz_enter")],
        [InlineKeyboardButton(text="⬅️ Back to Notifications", callback_data="notify_menu")], # Возврат в notify_menu
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
# ---

# =========================
#         SCHEDULE
# =========================

def schedule_menu() -> InlineKeyboardMarkup:
    """Главное меню раздела Schedule."""
    rows = [
        [
            InlineKeyboardButton(text="➕ Add", callback_data="sched_add"),
            InlineKeyboardButton(text="📃 List", callback_data="sched_list"),
        ],
        [
            InlineKeyboardButton(text="🗓️ Weekly View", callback_data="sched_weekly_view"),
            InlineKeyboardButton(text="🧹 Clear All", callback_data="sched_clear"),
        ],
        [
            InlineKeyboardButton(text="📤 Export", callback_data="sched_export"),
            InlineKeyboardButton(text="📥 Import", callback_data="sched_import"),
        ],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dow_picker_kb(mask: int) -> InlineKeyboardMarkup:
    """Компактный пикер дней недели."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # Кнопки дней недели в два ряда
    day_buttons = []
    for i, d in enumerate(days):
        label = f"{'✅ ' if (mask >> i) & 1 else ''}{d}"
        day_buttons.append(InlineKeyboardButton(text=label, callback_data=f"dow_t:{i}"))

    rows = [day_buttons[:4], day_buttons[4:]]

    # Кнопки пресетов
    rows.append([
        InlineKeyboardButton(text="Daily", callback_data="dow_all"),
        InlineKeyboardButton(text="Weekdays", callback_data="dow_wkd"),
        InlineKeyboardButton(text="Weekends", callback_data="dow_wke"),
    ])

    # Кнопки управления
    rows.append([
        InlineKeyboardButton(text="✅ OK", callback_data="dow_ok"),
        InlineKeyboardButton(text="⬅️ Back", callback_data="sched_menu"), # Возврат в меню Schedule
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def weekly_view_kb(days_info: list[tuple[str, str, int]]) -> InlineKeyboardMarkup:
    """Клавиатура для недельного просмотра."""
    rows = []
    for label, date_str, post_count in days_info:
        text = f"{label} ({post_count} posts)" if post_count > 0 else label
        rows.append([InlineKeyboardButton(text=text, callback_data=f"sched_day:{date_str}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back to Schedule", callback_data="sched_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def day_view_kb(date_str: str) -> InlineKeyboardMarkup:
    """Кнопка "назад" из дневного просмотра."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back to Weekly View", callback_data="sched_weekly_view")]
    ])

def job_list_kb(jobs: list[Job]) -> InlineKeyboardMarkup:
    """Клавиатура со списком задач для выбора (улучшенная)."""
    rows = []
    # Словарь для форматирования дней недели
    dow_map = {0: "Mo", 1: "Tu", 2: "We", 3: "Th", 4: "Fr", 5: "Sa", 6: "Su"}

    for job in jobs:
        # Форматирование дней недели
        dow_str = ""
        mask = job.dow_mask or 0
        if mask == 127: dow_str = "Daily"
        elif mask == 31: dow_str = "Weekdays"
        elif mask == 96: dow_str = "Weekends"
        else: dow_str = ",".join([dow_map[i] for i in range(7) if (mask >> i) & 1])

        # Количество медиа (если связь загружена)
        media_count = len(job.media) if hasattr(job, 'media') and job.media is not None else 0
        media_icon = f"🖼️{media_count}" if media_count > 0 else ""

        # Сокращение текста
        short_text = (job.text or "")[:25]
        if len(job.text or "") > 25: short_text += "..."

        label = f"⏰{job.time_str} 🗓️{dow_str} {media_icon}📝{short_text}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"sched_job_view:{job.id}")])

    rows.append([InlineKeyboardButton(text="⬅️ Back to Schedule", callback_data="sched_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def job_actions_kb(job_id: int) -> InlineKeyboardMarkup:
    """Кнопки действий для конкретной задачи."""
    rows = [
        [
            InlineKeyboardButton(text="✏️ Edit Text", callback_data=f"sched_job_edit_text:{job_id}"),
            # TODO: Добавить кнопки Edit Media, Edit Time/Days
            InlineKeyboardButton(text="🗑 Delete", callback_data=f"sched_job_delete:{job_id}")
        ],
        [InlineKeyboardButton(text="⬅️ Back to List", callback_data="sched_list")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def job_delete_confirm_kb(job_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления задачи."""
    rows = [
        [InlineKeyboardButton(text="✅ Yes, delete", callback_data=f"sched_job_delete_confirm:{job_id}")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data=f"sched_job_view:{job_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# =========================
#         ARCHIVE
# =========================

def archive_dates_kb(dates_with_counts: List[Tuple[str | date, int]]) -> InlineKeyboardMarkup: # Изменено на str | date
    """Клавиатура для выбора даты в архиве."""
    rows = []
    for dt_val, count in dates_with_counts:
        # --- (ИСПРАВЛЕНИЕ) ---
        # Преобразуем строку в date, если это строка
        dt: date | None = None # Явно указываем тип
        if isinstance(dt_val, str):
            try:
                # date.fromisoformat ожидает 'YYYY-MM-DD'
                dt = date.fromisoformat(dt_val)
            except ValueError:
                print(f"Warning: Could not parse date string '{dt_val}' in archive_dates_kb")
                continue # Пропускаем некорректные даты
        elif isinstance(dt_val, date):
            dt = dt_val
        else:
             print(f"Warning: Unexpected date type '{type(dt_val)}' in archive_dates_kb")
             continue # Пропускаем

        if dt is None: # Дополнительная проверка
             continue

        date_str = dt.strftime('%Y-%m-%d')
        # --- Конец исправления ---
        # Используем locale для форматирования, %B - полное название месяца
        label = dt.strftime(f'%d %b %Y ({count} posts)') # %b - сокращенное название месяца
        rows.append([InlineKeyboardButton(text=label, callback_data=f"archive_date:{date_str}")])

    rows.append([InlineKeyboardButton(text="📥 Import Manual Post", callback_data="archive_import_start")]) # Кнопка импорта
    rows.append([InlineKeyboardButton(text="⬅️ Back to Settings", callback_data="settings_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def archive_posts_kb(posts: List[PublishedPost], accounts_map: dict, date_str: str) -> InlineKeyboardMarkup:
    """Клавиатура со списком постов за выбранную дату."""
    rows = []
    for post in posts:
        time_str = post.published_at.strftime('%H:%M')
        acc_title = accounts_map.get(post.account_id, f"id={post.account_id}")
        media_icon = "🖼️" if post.has_media else ""
        short_text = (post.text or "")[:30].replace("\n", " ")
        if len(post.text or "") > 30: short_text += "..."
        label = f"{time_str} ({acc_title}) {media_icon} {short_text}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"archive_post:{post.id}")])

    rows.append([InlineKeyboardButton(text="⬅️ Back to Dates", callback_data="archive_list:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def archive_post_detail_kb(date_str: str, post_db_id: int) -> InlineKeyboardMarkup:
    """Кнопки действий для просмотра поста в архиве."""
    rows = [
        [
            InlineKeyboardButton(text="📊 Statistics", callback_data=f"archive_get_stats:{post_db_id}"),
            InlineKeyboardButton(text="🗣️ View Comments", callback_data=f"archive_view_comments:{post_db_id}")
        ],
        [InlineKeyboardButton(text="⬅️ Back to Posts", callback_data=f"archive_date:{date_str}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def archive_comments_kb(
    comments: list[dict], post_db_id: int, date_str: str,
    current_page: int, per_page: int,
    has_next_page: bool, has_prev_page: bool
) -> InlineKeyboardMarkup:
    """Клавиатура для отображения комментариев с пагинацией."""
    rows = []
    for i, comment in enumerate(comments):
        user = comment.get('username', 'unknown')
        text = (comment.get('text', '') or '')[:40].replace('\n', ' ')
        if len(comment.get('text', '')) > 40: text += '...'
        label = f"👤{user}: \"{text}\""
        comment_id = comment.get('id', '')
        # Кнопка для выбора комментария
        rows.append([InlineKeyboardButton(text=label, callback_data=f"archive_select_comment:{post_db_id}:{comment_id}")])

    # Кнопки пагинации
    nav_buttons = []
    if has_prev_page:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"archive_comments_page:{post_db_id}:prev"))
    else:
        # Добавляем неактивную кнопку для выравнивания
        nav_buttons.append(InlineKeyboardButton(text=" ", callback_data="noop"))

    nav_buttons.append(InlineKeyboardButton(text=f"- {current_page} -", callback_data="noop")) # Номер страницы

    if has_next_page:
        nav_buttons.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"archive_comments_page:{post_db_id}:next"))
    else:
        nav_buttons.append(InlineKeyboardButton(text=" ", callback_data="noop"))

    if nav_buttons:
        rows.append(nav_buttons)

    # Кнопка Назад к посту
    rows.append([InlineKeyboardButton(text="⬅️ Back to Post", callback_data=f"archive_post:{post_db_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def archive_comment_reply_kb(post_db_id: int, comment_id: str) -> InlineKeyboardMarkup:
    """Кнопки действий при ответе на комментарий."""
    rows = [
        [
            InlineKeyboardButton(text="🤖 Generate AI Reply", callback_data=f"archive_generate_reply:{post_db_id}:{comment_id}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Write Manually", callback_data=f"archive_write_reply:{post_db_id}:{comment_id}"),
        ],
        [
            # Возврат к списку комментариев (нужно передать ID поста)
            InlineKeyboardButton(text="⬅️ Back to Comments", callback_data=f"archive_view_comments:{post_db_id}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def archive_confirm_reply_kb(post_db_id: int, comment_id: str) -> InlineKeyboardMarkup:
    """Кнопки подтверждения/редактирования/отмены AI-ответа."""
    rows = [
        [
            InlineKeyboardButton(text="✅ Publish", callback_data=f"archive_publish_reply:{post_db_id}:{comment_id}"),
            InlineKeyboardButton(text="✏️ Edit", callback_data=f"archive_edit_reply:{post_db_id}:{comment_id}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Regenerate", callback_data=f"archive_generate_reply:{post_db_id}:{comment_id}"), # Повторный вызов генерации
            # Отмена возвращает к выбору действия (Generate/Write)
            InlineKeyboardButton(text="❌ Cancel", callback_data=f"archive_select_comment:{post_db_id}:{comment_id}")
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- (КЛАВИАТУРЫ ДЛЯ ИМПОРТА) ---
def archive_import_account_kb(accounts: List[Account]) -> InlineKeyboardMarkup:
    """Выбор аккаунта для импорта постов."""
    rows = []
    for acc in accounts:
        label = acc.title or f"Account {acc.id}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"archive_import_acc:{acc.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back to Archive", callback_data="archive_list:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def archive_import_list_kb(posts: List[dict]) -> InlineKeyboardMarkup:
    """Список постов для импорта."""
    rows = []
    for post in posts:
        ts_str = post.get('timestamp', '')
        dt_str = "Unknown time"
        if ts_str:
            try: dt_str = datetime.fromisoformat(ts_str.replace('Z', '+00:00')).strftime('%d %b %H:%M')
            except: pass # Ignore parsing errors
        short_text = (post.get('text', '') or '')[:40].replace("\n", " ")
        if len(post.get('text', '')) > 40: short_text += "..."
        media_icon = ""
        if post.get('media_type') in ('IMAGE', 'CAROUSEL', 'VIDEO'): media_icon = " 🖼️" # Исправлено на CAROUSEL

        label = f"{dt_str}{media_icon} \"{short_text}\""
        rows.append([InlineKeyboardButton(text=label, callback_data=f"archive_import_select:{post.get('id')}")])

    rows.append([InlineKeyboardButton(text="⬅️ Back to Archive", callback_data="archive_list:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- (НОВЫЕ КЛАВИАТУРЫ ДЛЯ ЧЕРНОВИКОВ) ---
def drafts_menu_kb(drafts: List[Draft]) -> InlineKeyboardMarkup:
    """Отображает список черновиков и кнопку 'Создать'."""
    rows = []
    for draft in drafts:
        # Отображаем начало текста и количество медиа
        short_text = (draft.text or "No text")[:40].replace("\n", " ")
        if len(draft.text or "") > 40: short_text += "..."
        # (ИСПРАВЛЕНИЕ) Проверяем, существует ли атрибут media перед подсчетом
        media_count = len(draft.media) if hasattr(draft, 'media') and draft.media is not None else 0
        media_icon = f"🖼️{media_count}" if media_count > 0 else ""
        label = f"📄{draft.id}: {media_icon} \"{short_text}\""
        rows.append([InlineKeyboardButton(text=label, callback_data=f"draft_view:{draft.id}")])

    rows.append([InlineKeyboardButton(text="➕ Create New Draft", callback_data="draft_create")])
    rows.append([InlineKeyboardButton(text="⬅️ Back to Main Menu", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def draft_view_kb(draft_id: int) -> InlineKeyboardMarkup:
    """Кнопки действий для просмотра/редактирования черновика."""
    rows = [
        [
            InlineKeyboardButton(text="✏️ Edit Text", callback_data=f"draft_edit_text:{draft_id}"),
            InlineKeyboardButton(text="🖼️ Manage Media", callback_data=f"draft_manage_media:{draft_id}"),
        ],
        [
            InlineKeyboardButton(text="✨ Suggest Hashtags", callback_data=f"draft_suggest_hashtags:{draft_id}"),
            InlineKeyboardButton(text="📋 Copy for Threads", callback_data=f"draft_copy:{draft_id}"),
        ],
        [
            InlineKeyboardButton(text="🗑️ Delete Draft", callback_data=f"draft_delete:{draft_id}"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Back to Drafts List", callback_data="drafts_menu"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def draft_manage_media_kb(draft_id: int, has_media: bool) -> InlineKeyboardMarkup:
    """Кнопки управления медиа для черновика."""
    rows = [
        # Кнопка 'Clear' активна только если есть медиа
        [InlineKeyboardButton(text="♻️ Clear All Media", callback_data=f"draft_clear_media:{draft_id}") if has_media else InlineKeyboardButton(text=" ", callback_data="noop")],
        [InlineKeyboardButton(text="⬅️ Back to Draft", callback_data=f"draft_view:{draft_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def draft_copy_kb(draft_id: int) -> InlineKeyboardMarkup:
    """Кнопка 'Назад' после копирования текста."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back to Draft", callback_data=f"draft_view:{draft_id}")]
    ])

def draft_delete_confirm_kb(draft_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления черновика."""
    rows = [
        [InlineKeyboardButton(text="✅ Yes, delete", callback_data=f"draft_delete_confirm:{draft_id}")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data=f"draft_view:{draft_id}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- Конец клавиатур для черновиков ---

# Общая кнопка для возврата
def back_button(callback_data: str) -> InlineKeyboardMarkup:
    """Простая клавиатура с одной кнопкой 'Назад'."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Back", callback_data=callback_data)]
    ])

# Пустая кнопка для колбеков, где не нужна клавиатура
noop_kb = InlineKeyboardMarkup(inline_keyboard=[[]])

