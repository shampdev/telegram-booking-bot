"""Красивый вывод дат, записей и прочего.

Стиль сообщений: без эмодзи. Заголовки — жирным, сама запись — «карточкой»
в виде цитаты (<blockquote>), второстепенное — курсивом."""
import html
import re
from datetime import date, datetime

from settings import DT_FMT, WORK_DAYS, WORK_END, WORK_START

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
              "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, DT_FMT)


def esc(value) -> str:
    return html.escape(str(value))


def card(*lines: str) -> str:
    """Карточка: строки в цитате с полоской слева."""
    return "<blockquote>" + "\n".join(line for line in lines if line) + "</blockquote>"


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


def step(n: int, total: int = 4) -> str:
    """<i>Шаг 2 из 4</i>"""
    return f"<i>Шаг {n} из {total}</i>"


def stars(rating: int) -> str:
    return "★" * rating + "☆" * (5 - rating)


def plural(n: int, one: str, few: str, many: str) -> str:
    """plural(5, 'отзыв', 'отзыва', 'отзывов') -> '5 отзывов'"""
    if n % 10 == 1 and n % 100 != 11:
        word = one
    elif 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        word = few
    else:
        word = many
    return f"{n} {word}"


def fmt_rating(master: dict) -> str:
    """★ 4.8 · 12 отзывов  или  новый мастер"""
    if not master.get("reviews_count"):
        return "новый мастер"
    return f"★ {master['rating']} · {plural(master['reviews_count'], 'отзыв', 'отзыва', 'отзывов')}"


def booking_lines(b: dict) -> list[str]:
    start, end = parse_dt(b["start_at"]), parse_dt(b["end_at"])
    return [
        f"<b>{fmt_day_long(start.date())}, {start:%H:%M}–{end:%H:%M}</b>",
        f"{esc(b['service_name'])} — {b['price']} ₽",
        f"Мастер: {esc(b['master_name'])}",
    ]


def fmt_booking(b: dict) -> str:
    return card(*booking_lines(b))


def fmt_booking_admin(b: dict) -> str:
    return card(
        *booking_lines(b),
        f'Клиент: {esc(b["client_name"])}, {esc(b["phone"])} '
        f'(<a href="tg://user?id={b["user_id"]}">профиль</a>)',
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
