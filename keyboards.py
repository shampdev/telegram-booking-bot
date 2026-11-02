"""Клавиатуры и callback-данные."""
from datetime import date, datetime, timedelta

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils.formatting import fmt_day_short, parse_dt

BTN_BOOK = "📅 Записаться"
BTN_MY = "🗂 Мои записи"
BTN_ABOUT = "ℹ️ О нас"
BTN_ABORT = "❌ Отмена"


# ---------- Callback-данные ----------
# Разделитель в callback_data — двоеточие, поэтому время и даты передаём без него.

class ServiceCB(CallbackData, prefix="srv"):
    id: int


class MasterCB(CallbackData, prefix="mst"):
    id: int


class DayCB(CallbackData, prefix="day"):
    value: str  # 20261103


class TimeCB(CallbackData, prefix="tm"):
    value: str  # 1030


class NavCB(CallbackData, prefix="nav"):
    to: str  # services | masters | days | times | confirm | abort


class MyCB(CallbackData, prefix="my"):
    action: str  # ask | yes | list
    id: int = 0


class AdminCB(CallbackData, prefix="adm"):
    action: str  # menu | pick | day | broadcast | bc_send
    value: str = ""


class AdminCancelCB(CallbackData, prefix="admc"):
    id: int
    day: str


# ---------- Обычные клавиатуры ----------

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK)],
            [KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_ABOUT)],
        ],
        resize_keyboard=True,
    )


def phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)],
            [KeyboardButton(text=BTN_ABORT)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


# ---------- Шаги записи ----------

def _nav_row(back_to: str | None) -> list[InlineKeyboardButton]:
    row = []
    if back_to:
        row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=NavCB(to=back_to).pack()))
    row.append(InlineKeyboardButton(text="✖️ Отмена", callback_data=NavCB(to="abort").pack()))
    return row


def services_kb(services: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in services:
        kb.button(text=f"{s['name']} · {s['price']} ₽ · {s['duration']} мин", callback_data=ServiceCB(id=s["id"]))
    kb.adjust(1)
    kb.row(*_nav_row(None))
    return kb.as_markup()


def masters_kb(masters: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for m in masters:
        kb.button(text=m["name"], callback_data=MasterCB(id=m["id"]))
    kb.adjust(2)
    kb.row(*_nav_row("services"))
    return kb.as_markup()


def days_kb(days: list[date]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d in days:
        kb.button(text=fmt_day_short(d), callback_data=DayCB(value=d.strftime("%Y%m%d")))
    kb.adjust(3)
    kb.row(*_nav_row("masters"))
    return kb.as_markup()


def times_kb(slots: list[datetime]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in slots:
        kb.button(text=t.strftime("%H:%M"), callback_data=TimeCB(value=t.strftime("%H%M")))
    kb.adjust(4)
    kb.row(*_nav_row("days"))
    return kb.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить запись", callback_data=NavCB(to="confirm").pack())],
        _nav_row("times"),
    ])


# ---------- Мои записи ----------

def my_bookings_kb(bookings: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for b in bookings:
        start = parse_dt(b["start_at"])
        kb.button(text=f"Отменить {start:%d.%m %H:%M}", callback_data=MyCB(action="ask", id=b["id"]))
    kb.adjust(1)
    return kb.as_markup()


def my_cancel_confirm_kb(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, отменить", callback_data=MyCB(action="yes", id=booking_id).pack()),
        InlineKeyboardButton(text="⬅️ Нет", callback_data=MyCB(action="list").pack()),
    ]])


# ---------- Админка ----------

def admin_menu_kb(today: date) -> InlineKeyboardMarkup:
    tomorrow = today + timedelta(days=1)
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 Сегодня", callback_data=AdminCB(action="day", value=today.strftime("%Y%m%d")))
    kb.button(text="📋 Завтра", callback_data=AdminCB(action="day", value=tomorrow.strftime("%Y%m%d")))
    kb.button(text="🗓 Выбрать дату", callback_data=AdminCB(action="pick"))
    kb.button(text="📣 Рассылка", callback_data=AdminCB(action="broadcast"))
    kb.adjust(2)
    return kb.as_markup()


def admin_days_kb(today: date, count: int = 14) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i in range(count):
        d = today + timedelta(days=i)
        kb.button(text=fmt_day_short(d), callback_data=AdminCB(action="day", value=d.strftime("%Y%m%d")))
    kb.adjust(3)
    kb.row(InlineKeyboardButton(text="⬅️ Меню", callback_data=AdminCB(action="menu").pack()))
    return kb.as_markup()


def admin_day_kb(bookings: list[dict], day_value: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for b in bookings:
        start = parse_dt(b["start_at"])
        kb.button(
            text=f"❌ {start:%H:%M} {b['client_name']}",
            callback_data=AdminCancelCB(id=b["id"], day=day_value),
        )
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(text="🗓 Другая дата", callback_data=AdminCB(action="pick").pack()),
        InlineKeyboardButton(text="⬅️ Меню", callback_data=AdminCB(action="menu").pack()),
    )
    return kb.as_markup()


def broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📤 Отправить", callback_data=AdminCB(action="bc_send").pack()),
        InlineKeyboardButton(text="✖️ Отмена", callback_data=AdminCB(action="menu").pack()),
    ]])
