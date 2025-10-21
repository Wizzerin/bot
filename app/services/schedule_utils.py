# app/services/schedule_utils.py
# ------------------------------------------------------------
# Утилиты для работы с маской дней недели (пн..вс) в виде битов.
# Битовое представление (LSB -> Monday):
#   0: Mon, 1: Tue, 2: Wed, 3: Thu, 4: Fri, 5: Sat, 6: Sun
# Примеры:
#   127 (0b1111111) — все дни,
#   31  (0b011111)  — будни (Mon..Fri),
#   96  (0b1100000) — выходные (Sat..Sun).
# ------------------------------------------------------------

from __future__ import annotations

from typing import Optional

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
    Возвращает:
      - "mon,tue,..." если выбраны конкретные дни;
      - None — если mask None или эквивалентна "все дни".
    """
    if mask is None:
        return None

    mask = int(mask) & 0b1111111
    if mask == 0 or mask == all_days_mask():
        # 'None' для CronTrigger означает «каждый день»
        return None

    days = [name for i, name in enumerate(_DOW_CRON) if (mask & (1 << i))]
    return ",".join(days) if days else None


def mask_to_human(mask: Optional[int]) -> str:
    """
    Возвращает человекочитаемую подпись:
      - "Daily" — все дни
      - "Weekdays" — будни
      - "Weekends" — выходные
      - "Mon, Wed, Fri" — перечисление конкретных дней
      - "—" — если mask пустая/0
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
