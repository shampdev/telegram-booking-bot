"""Клавиатуры и callback-данные."""
import calendar
from datetime import date, datetime, timedelta

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.formatting import MONTHS_NOM, WEEKDAYS, fmt_day_short, parse_dt

BTN_BOOK = "Записаться"
BTN_MY = "Мои записи"
BTN_ABOUT = "О салоне"
BTN_ABORT = "Отмена"

MENU_BUTTONS = {BTN_BOOK, BTN_MY, BTN_ABOUT, BTN_ABORT}

BLANK = "\u2800"  # «невидимый» символ: Telegram не разрешает кнопки с пустым текстом


# ---------- Callback-данные ----------
# Разделитель в callback_data — двоеточие, поэтому время и даты передаём без него.

class ServiceCB(CallbackData, prefix="srv"):
    id: int


class MasterCB(CallbackData, prefix="mst"):
    id: int


class DayCB(CallbackData, prefix="day"):
    value: str  # 20261103


class MonthCB(CallbackData, prefix="mon"):
    value: str  # 202612


class TimeCB(CallbackData, prefix="tm"):
    value: str  # 1030


class NavCB(CallbackData, prefix="nav"):
    to: str  # services | masters | days | times | confirm | abort


class NoopCB(CallbackData, prefix="noop"):
    pass


class MyCB(CallbackData, prefix="my"):
    action: str  # ask | yes | list
    id: int = 0


class ReviewCB(CallbackData, prefix="rv"):
    booking_id: int
    rating: int  # 0 = пропустить комментарий


class AdminCB(CallbackData, prefix="adm"):
    action: str
    value: str = ""


class AdminCancelCB(CallbackData, prefix="admc"):
    id: int
    day: str


class AdminItemCB(CallbackData, prefix="admi"):
    kind: str    # srv | mst
    action: str  # open | toggle | price
    id: int


NOOP = NoopCB().pack()


def _noop(text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=NOOP)


# ---------- Обычные клавиатуры ----------

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK)],
            [KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_ABOUT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие в меню",
    )


def phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Отправить мой номер", request_contact=True)],
            [KeyboardButton(text=BTN_ABORT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="+7 999 123-45-67",
    )


# ---------- Шаги записи ----------

def _nav_row(back_to: str | None) -> list[InlineKeyboardButton]:
    row = []
    if back_to:
        row.append(InlineKeyboardButton(text="‹ Назад", callback_data=NavCB(to=back_to).pack()))
    row.append(InlineKeyboardButton(text="Отмена", callback_data=NavCB(to="abort").pack()))
    return row


def services_kb(services: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in services:
        kb.button(text=f"{s['name']} · {s['price']} ₽", callback_data=ServiceCB(id=s["id"]))
    kb.adjust(1)
    kb.row(*_nav_row(None))
    return kb.as_markup()


def masters_kb(masters: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for m in masters:
        rating = f" · ★ {m['rating']}" if m.get("reviews_count") else ""
        kb.button(text=f"{m['name']}{rating}", callback_data=MasterCB(id=m["id"]))
    kb.adjust(1)
    kb.row(*_nav_row("services"))
    return kb.as_markup()


def calendar_kb(month: date, available: set[date], first_day: date, last_day: date) -> InlineKeyboardMarkup:
    """Календарь на месяц.
    month — любой день нужного месяца; available — дни со свободными окнами;
    first_day/last_day — границы, в которых можно записаться (для показа и листания)."""
    rows = []

    # Заголовок с листанием месяцев
    prev_month = (month.replace(day=1) - timedelta(days=1)).replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    can_prev = prev_month >= first_day.replace(day=1)
    can_next = next_month <= last_day
    rows.append([
        InlineKeyboardButton(text="‹", callback_data=MonthCB(value=f"{prev_month:%Y%m}").pack())
        if can_prev else _noop(BLANK),
        _noop(f"{MONTHS_NOM[month.month - 1]} {month.year}"),
        InlineKeyboardButton(text="›", callback_data=MonthCB(value=f"{next_month:%Y%m}").pack())
        if can_next else _noop(BLANK),
    ])
    rows.append([_noop(d) for d in WEEKDAYS])

    # Показываем только недели, которые пересекаются с периодом записи
    for week in calendar.Calendar().monthdatescalendar(month.year, month.month):
        if week[-1] < first_day or week[0] > last_day:
            continue
        row = []
        for d in week:
            if d.month != month.month:
                row.append(_noop(BLANK))
            elif d in available:
                row.append(InlineKeyboardButton(text=str(d.day), callback_data=DayCB(value=f"{d:%Y%m%d}").pack()))
            else:
                row.append(_noop("·"))
        rows.append(row)

    rows.append(_nav_row("masters"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


DAY_PARTS = [("Утро", 0, 12), ("День", 12, 17), ("Вечер", 17, 24)]


def times_kb(slots: list[datetime]) -> InlineKeyboardMarkup:
    """Свободное время, сгруппированное по частям дня."""
    rows = []
    for title, start_h, end_h in DAY_PARTS:
        part = [t for t in slots if start_h <= t.hour < end_h]
        if not part:
            continue
        rows.append([_noop(title)])
        for i in range(0, len(part), 4):
            chunk = [
                InlineKeyboardButton(text=t.strftime("%H:%M"), callback_data=TimeCB(value=t.strftime("%H%M")).pack())
                for t in part[i:i + 4]
            ]
            chunk += [_noop(BLANK)] * (4 - len(chunk))  # выравниваем сетку
            rows.append(chunk)
    rows.append(_nav_row("days"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подтвердить запись", callback_data=NavCB(to="confirm").pack())],
        _nav_row("times"),
    ])


# ---------- Мои записи ----------

def my_bookings_kb(bookings: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for b in bookings:
        start = parse_dt(b["start_at"])
        kb.button(text=f"Отменить {start:%d.%m в %H:%M}", callback_data=MyCB(action="ask", id=b["id"]))
    kb.adjust(1)
    return kb.as_markup()


def my_cancel_confirm_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, отменить", callback_data=MyCB(action="yes", id=booking_id).pack()),
        InlineKeyboardButton(text="Нет, оставить", callback_data=MyCB(action="list").pack()),
    ]])


# ---------- Отзывы ----------

def rating_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{n} ★", callback_data=ReviewCB(booking_id=booking_id, rating=n).pack())
         for n in range(1, 6)]
    ])


def skip_comment_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Пропустить", callback_data=ReviewCB(booking_id=booking_id, rating=0).pack())
    ]])


# ---------- Админка ----------

def _admin_back(action: str = "menu", text: str = "‹ Меню") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=AdminCB(action=action).pack())


def admin_menu_kb(today: date) -> InlineKeyboardMarkup:
    tomorrow = today + timedelta(days=1)
    kb = InlineKeyboardBuilder()
    kb.button(text="Сегодня", callback_data=AdminCB(action="day", value=f"{today:%Y%m%d}"))
    kb.button(text="Завтра", callback_data=AdminCB(action="day", value=f"{tomorrow:%Y%m%d}"))
    kb.button(text="Выбрать дату", callback_data=AdminCB(action="pick"))
    kb.button(text="Рассылка", callback_data=AdminCB(action="broadcast"))
    kb.button(text="Услуги", callback_data=AdminCB(action="services"))
    kb.button(text="Мастера", callback_data=AdminCB(action="masters"))
    kb.button(text="Отзывы", callback_data=AdminCB(action="reviews"))
    kb.button(text="Выгрузка в Excel", callback_data=AdminCB(action="export"))
    kb.adjust(2)
    return kb.as_markup()


def admin_days_kb(today: date, count: int = 14) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i in range(count):
        d = today + timedelta(days=i)
        kb.button(text=fmt_day_short(d), callback_data=AdminCB(action="day", value=f"{d:%Y%m%d}"))
    kb.adjust(3)
    kb.row(_admin_back())
    return kb.as_markup()


def admin_day_kb(bookings: list[dict], day_value: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for b in bookings:
        start = parse_dt(b["start_at"])
        kb.button(
            text=f"Отменить {start:%H:%M} · {b['client_name']}",
            callback_data=AdminCancelCB(id=b["id"], day=day_value),
        )
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(text="Другая дата", callback_data=AdminCB(action="pick").pack()),
        _admin_back(),
    )
    return kb.as_markup()


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Отправить", callback_data=AdminCB(action="bc_send").pack()),
        _admin_back(text="Отмена"),
    ]])


def admin_list_kb(kind: str, items: list[dict], add_text: str) -> InlineKeyboardMarkup:
    """Список услуг или мастеров. Скрытые помечены «(скрыта)»."""
    kb = InlineKeyboardBuilder()
    for item in items:
        mark = "" if item["is_active"] else " (скрыто)"
        price = f" · {item['price']} ₽" if kind == "srv" else ""
        kb.button(text=f"{item['name']}{price}{mark}", callback_data=AdminItemCB(kind=kind, action="open", id=item["id"]))
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text=add_text, callback_data=AdminCB(action=f"add_{kind}").pack()))
    kb.row(_admin_back())
    return kb.as_markup()


def admin_item_kb(kind: str, item: dict) -> InlineKeyboardMarkup:
    rows = []
    if kind == "srv":
        rows.append([InlineKeyboardButton(
            text="Изменить цену", callback_data=AdminItemCB(kind=kind, action="price", id=item["id"]).pack()
        )])
    toggle_text = "Скрыть от клиентов" if item["is_active"] else "Снова показывать"
    rows.append([InlineKeyboardButton(
        text=toggle_text, callback_data=AdminItemCB(kind=kind, action="toggle", id=item["id"]).pack()
    )])
    back = "services" if kind == "srv" else "masters"
    rows.append([_admin_back(back, "‹ К списку")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_cancel_input_kb(back_action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_admin_back(back_action, "Отмена")]])


def admin_skip_kb(action: str, back_action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Пропустить", callback_data=AdminCB(action=action).pack()),
        _admin_back(back_action, "Отмена"),
    ]])


def admin_export_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Текущий месяц", callback_data=AdminCB(action="xls", value="this"))
    kb.button(text="Прошлый месяц", callback_data=AdminCB(action="xls", value="prev"))
    kb.button(text="За всё время", callback_data=AdminCB(action="xls", value="all"))
    kb.adjust(1)
    kb.row(_admin_back())
    return kb.as_markup()


def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_admin_back()]])
