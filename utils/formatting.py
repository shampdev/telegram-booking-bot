"""Красивый вывод дат и записей."""
import html
import re
from datetime import date, datetime

from settings import DT_FMT, WORK_DAYS, WORK_END, WORK_START

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, DT_FMT)


def fmt_day_short(d: date) -> str:
    """Пн 03.11"""
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m}"


def fmt_day_long(d: date) -> str:
    """3 ноября, пн"""
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS[d.weekday()].lower()}"


def fmt_work_hours() -> str:
    days = sorted(WORK_DAYS)
    if days == list(range(days[0], days[-1] + 1)):
        day_part = f"{WEEKDAYS[days[0]]}–{WEEKDAYS[days[-1]]}"
    else:
        day_part = ", ".join(WEEKDAYS[d] for d in days)
    return f"{day_part}, {WORK_START:%H:%M}–{WORK_END:%H:%M}"


def fmt_booking(b: dict) -> str:
    start = parse_dt(b["start_at"])
    return (
        f"📅 <b>{fmt_day_long(start.date())}, {start:%H:%M}</b>\n"
        f"💈 {html.escape(b['service_name'])} — {b['price']} ₽\n"
        f"👤 Мастер: {html.escape(b['master_name'])}"
    )


def fmt_booking_admin(b: dict) -> str:
    return (
        fmt_booking(b)
        + f"\n🙋 Клиент: {html.escape(b['client_name'])}, {html.escape(b['phone'])}"
        + f' (<a href="tg://user?id={b["user_id"]}">профиль</a>)'
    )


def normalize_phone(text: str) -> str | None:
    """Приводит номер к виду +79991234567. Возвращает None, если это не похоже на телефон."""
    if re.search(r"[^\d\s+\-()]", text):
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    if 11 <= len(digits) <= 15:
        return "+" + digits
    return None
