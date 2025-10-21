# app/services/schedule_utils.py
# ------------------------------------------------------------
# Утилиты для работы с маской дней недели и временем.
# ------------------------------------------------------------

from __future__ import annotations
from typing import Optional, Tuple

# Метки для человека и для CRON
_DOW_HUMAN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_DOW_CRON  = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def all_days_mask() -> int:
    """Все дни недели."""
    return 0b1111111  # 127


def weekdays_mask() -> int:
    """Будни (Mon..Fri)."""
    return 0b0011111  # 31


def weekends_mask() -> int:
    """Выходные (Sat..Sun)."""
    return 0b1100000  # 96


def toggle_day(mask: int, idx: int) -> int:
    """Переключить день idx (0..6) в маске."""
    if not (0 <= idx <= 6):
        return mask
    return mask ^ (1 << idx)


def mask_to_cron(mask: Optional[int]) -> Optional[str]:
    """
    Переводит битовую маску дней в строку для APScheduler/CronTrigger(day_of_week=...).
    """
    if mask is None:
        return None

    mask = int(mask) & 0b1111111
    if mask == 0 or mask == all_days_mask():
        return None

    days = [name for i, name in enumerate(_DOW_CRON) if (mask & (1 << i))]
    return ",".join(days) if days else None


def mask_to_human(mask: Optional[int]) -> str:
    """
    Возвращает человекочитаемую подпись: "Daily", "Weekdays", "Mon, Wed, Fri" и т.д.
    """
    if mask is None:
        return "Daily"

    mask = int(mask) & 0b1111111
    if mask == 0:
        return "—"
    if mask == all_days_mask():
        return "Daily"
    if mask == weekdays_mask():
        return "Weekdays"
    if mask == weekends_mask():
        return "Weekends"

    days = [name for i, name in enumerate(_DOW_HUMAN) if (mask & (1 << i))]
    return ", ".join(days) if days else "—"

# (ИЗМЕНЕНО) Добавлен алиас для обратной совместимости
mask_to_days_label = mask_to_human

def parse_days_to_mask(value: str | None) -> int | None:
    """
    Преобразует текстовое описание дней (Daily, Weekdays, Mon,Wed) в битовую маску.
    """
    if not value:
        return None
    s = value.strip().lower()
    if not s:
        return None
    
    _SHORT2IDX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

    if s in ("daily",):
        return all_days_mask()
    if s in ("weekdays", "wd", "workdays"):
        return weekdays_mask()
    if s in ("weekends", "we"):
        return weekends_mask()
    
    items = [x.strip() for x in s.replace(";", ",").split(",") if x.strip()]
    mask = 0
    for it in items:
        idx = _SHORT2IDX.get(it[:3])
        if idx is None:
            return None
        mask |= (1 << idx)
    return mask or None


def _parse_hhmm(s: str) -> Tuple[bool, str | None]:
    """Проверяет, является ли строка валидным временем HH:MM."""
    try:
        hh, mm = s.strip().split(":")
        hh_i, mm_i = int(hh), int(mm)
        if 0 <= hh_i <= 23 and 0 <= mm_i <= 59:
            return True, None
    except Exception:
        pass
    return False, "Time must be HH:MM (00-23:00-59)."

