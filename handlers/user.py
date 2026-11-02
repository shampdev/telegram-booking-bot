"""Сценарий клиента: запись, мои записи, информация о салоне."""
import html
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database import Database
from keyboards import (
    BTN_ABORT, BTN_ABOUT, BTN_BOOK, BTN_MY,
    DayCB, MasterCB, MyCB, NavCB, ServiceCB, TimeCB,
    confirm_kb, days_kb, main_menu, masters_kb, my_bookings_kb, my_cancel_confirm_kb,
    phone_kb, services_kb, times_kb,
)
from settings import DT_FMT, REMIND_BEFORE_HOURS, SALON_ADDRESS, SALON_NAME, SALON_PHONE
from states import Booking
from utils.formatting import fmt_booking, fmt_booking_admin, fmt_day_long, fmt_work_hours, normalize_phone
from utils.notify import notify_admins
from utils.schedule import free_slots, upcoming_days

router = Router(name="user")


# ---------- Экраны шагов записи ----------
# Каждая функция возвращает текст и клавиатуру, чтобы один и тот же экран
# можно было показать и при движении вперёд, и по кнопке «Назад».

async def services_view(db: Database):
    return "<b>Шаг 1 из 4.</b> Выберите услугу:", services_kb(await db.get_services())


async def masters_view(db: Database, data: dict):
    service = await db.get_service(data["service_id"])
    return (
        f"💈 {html.escape(service['name'])}\n\n<b>Шаг 2 из 4.</b> Выберите мастера:",
        masters_kb(await db.get_masters()),
    )


async def days_view(db: Database, config: Config, data: dict):
    service = await db.get_service(data["service_id"])
    master = await db.get_master(data["master_id"])
    now = config.now()

    days = []
    for d in upcoming_days(now.date()):
        busy = await db.get_busy_intervals(master["id"], d)
        if free_slots(d, service["duration"], busy, now):
            days.append(d)

    header = f"💈 {html.escape(service['name'])}\n👤 {html.escape(master['name'])}\n\n"
    if not days:
        return header + "😔 У этого мастера нет свободных окон в ближайшие две недели. Выберите другого.", days_kb([])
    return header + "<b>Шаг 3 из 4.</b> Выберите день:", days_kb(days)


async def times_view(db: Database, config: Config, data: dict):
    service = await db.get_service(data["service_id"])
    day = date.fromisoformat(data["day"])
    busy = await db.get_busy_intervals(data["master_id"], day)
    slots = free_slots(day, service["duration"], busy, config.now())
    if not slots:
        return f"📅 {fmt_day_long(day)}\n\n😔 На этот день свободного времени не осталось.", times_kb([])
    return f"📅 {fmt_day_long(day)}\n\n<b>Шаг 4 из 4.</b> Выберите время:", times_kb(slots)


async def summary_text(db: Database, data: dict) -> str:
    service = await db.get_service(data["service_id"])
    master = await db.get_master(data["master_id"])
    start = datetime.strptime(data["start_at"], DT_FMT)
    return (
        "<b>Проверьте запись:</b>\n\n"
        f"📅 <b>{fmt_day_long(start.date())}, {start:%H:%M}</b>\n"
        f"💈 {html.escape(service['name'])} — {service['price']} ₽ ({service['duration']} мин)\n"
        f"👤 Мастер: {html.escape(master['name'])}\n"
        f"🙋 {html.escape(data['client_name'])}, {data['phone']}"
    )


# ---------- Старт и главное меню ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database):
    await state.clear()
    user = message.from_user
    await db.add_user(user.id, user.username, user.first_name)
    await message.answer(
        f"Привет, {html.escape(user.first_name or 'друг')}! 👋\n\n"
        f"Я бот <b>{SALON_NAME}</b>. Помогу записаться на стрижку за минуту — "
        "без звонков и ожидания ответа.",
        reply_markup=main_menu(),
    )


@router.message(Command("cancel"))
@router.message(F.text == BTN_ABORT)
async def abort_by_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


@router.callback_query(NavCB.filter(F.to == "abort"))
async def abort_by_button(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Запись отменена. Возвращайтесь, когда будет удобно 🙂")
    await callback.answer()


@router.message(F.text == BTN_ABOUT)
async def about(message: Message, db: Database):
    services = await db.get_services()
    prices = "\n".join(f"• {html.escape(s['name'])} — {s['price']} ₽" for s in services)
    await message.answer(
        f"<b>{SALON_NAME}</b>\n\n"
        f"📍 {SALON_ADDRESS}\n"
        f"🕙 {fmt_work_hours()}\n"
        f"📞 {SALON_PHONE}\n\n"
        f"<b>Цены:</b>\n{prices}",
        reply_markup=main_menu(),
    )


# ---------- Запись: шаги 1–4 ----------

@router.message(F.text == BTN_BOOK)
async def start_booking(message: Message, state: FSMContext, db: Database):
    await state.clear()
    await state.set_state(Booking.service)
    text, kb = await services_view(db)
    await message.answer(text, reply_markup=kb)


@router.callback_query(Booking.service, ServiceCB.filter())
async def pick_service(callback: CallbackQuery, callback_data: ServiceCB, state: FSMContext, db: Database):
    if not await db.get_service(callback_data.id):
        return await callback.answer("Услуга не найдена", show_alert=True)
    await state.update_data(service_id=callback_data.id)
    await state.set_state(Booking.master)
    text, kb = await masters_view(db, await state.get_data())
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(Booking.master, MasterCB.filter())
async def pick_master(
    callback: CallbackQuery, callback_data: MasterCB, state: FSMContext, db: Database, config: Config
):
    if not await db.get_master(callback_data.id):
        return await callback.answer("Мастер не найден", show_alert=True)
    await state.update_data(master_id=callback_data.id)
    await state.set_state(Booking.day)
    text, kb = await days_view(db, config, await state.get_data())
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(Booking.day, DayCB.filter())
async def pick_day(callback: CallbackQuery, callback_data: DayCB, state: FSMContext, db: Database, config: Config):
    day = datetime.strptime(callback_data.value, "%Y%m%d").date()
    await state.update_data(day=day.isoformat())
    await state.set_state(Booking.time)
    text, kb = await times_view(db, config, await state.get_data())
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(Booking.time, TimeCB.filter())
async def pick_time(callback: CallbackQuery, callback_data: TimeCB, state: FSMContext, db: Database, config: Config):
    data = await state.get_data()
    start = datetime.combine(date.fromisoformat(data["day"]), datetime.strptime(callback_data.value, "%H%M").time())

    # Время могло пройти или освободиться/заняться, пока клиент думал
    service = await db.get_service(data["service_id"])
    busy = await db.get_busy_intervals(data["master_id"], start.date())
    if start not in free_slots(start.date(), service["duration"], busy, config.now()):
        text, kb = await times_view(db, config, data)
        await callback.message.edit_text("😔 Это время уже недоступно.\n\n" + text, reply_markup=kb)
        return await callback.answer()

    await state.update_data(start_at=start.strftime(DT_FMT))
    await state.set_state(Booking.name)
    await callback.message.edit_text(
        f"Отлично, {fmt_day_long(start.date())} в {start:%H:%M} 👌\n\nКак к вам обращаться? Напишите имя."
    )
    await callback.answer()


# ---------- Кнопка «Назад» ----------

@router.callback_query(StateFilter(Booking), NavCB.filter(F.to.in_({"services", "masters", "days", "times"})))
async def go_back(callback: CallbackQuery, callback_data: NavCB, state: FSMContext, db: Database, config: Config):
    data = await state.get_data()
    if callback_data.to == "services":
        await state.set_state(Booking.service)
        text, kb = await services_view(db)
    elif callback_data.to == "masters":
        await state.set_state(Booking.master)
        text, kb = await masters_view(db, data)
    elif callback_data.to == "days":
        await state.set_state(Booking.day)
        text, kb = await days_view(db, config, data)
    else:
        await state.set_state(Booking.time)
        text, kb = await times_view(db, config, data)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------- Контактные данные ----------

@router.message(Booking.name, F.text, ~F.text.startswith("/"))
async def enter_name(message: Message, state: FSMContext):
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 50:
        return await message.answer("Имя должно быть от 2 до 50 символов. Попробуйте ещё раз.")
    await state.update_data(client_name=name)
    await state.set_state(Booking.phone)
    await message.answer(
        "Оставьте номер телефона — мастер свяжется, если что-то изменится.\n\n"
        "Нажмите кнопку ниже или напишите номер, например <code>+7 999 123-45-67</code>.",
        reply_markup=phone_kb(),
    )


@router.message(Booking.phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext, db: Database):
    phone = normalize_phone(message.contact.phone_number) or message.contact.phone_number
    await _save_phone(message, state, db, phone)


@router.message(Booking.phone, F.text, ~F.text.startswith("/"))
async def enter_phone_text(message: Message, state: FSMContext, db: Database):
    phone = normalize_phone(message.text)
    if not phone:
        return await message.answer("Не похоже на номер телефона 🤔 Пример: <code>+7 999 123-45-67</code>")
    await _save_phone(message, state, db, phone)


async def _save_phone(message: Message, state: FSMContext, db: Database, phone: str):
    await state.update_data(phone=phone)
    await state.set_state(Booking.confirm)
    # Убираем клавиатуру с кнопкой «Отправить номер», возвращаем главное меню
    await message.answer("Почти готово!", reply_markup=main_menu())
    await message.answer(await summary_text(db, await state.get_data()), reply_markup=confirm_kb())


# ---------- Подтверждение ----------

@router.callback_query(Booking.confirm, NavCB.filter(F.to == "confirm"))
async def confirm_booking(callback: CallbackQuery, state: FSMContext, db: Database, config: Config, bot: Bot):
    data = await state.get_data()
    service = await db.get_service(data["service_id"])
    start = datetime.strptime(data["start_at"], DT_FMT)
    now = config.now()

    booking_id = None
    if start > now:
        # Если до визита меньше, чем время напоминания, напоминать уже не нужно
        soon = start - now <= timedelta(hours=REMIND_BEFORE_HOURS)
        booking_id = await db.create_booking(
            user_id=callback.from_user.id,
            client_name=data["client_name"],
            phone=data["phone"],
            service_id=service["id"],
            master_id=data["master_id"],
            start=start,
            duration=service["duration"],
            reminded=soon,
        )

    if booking_id is None:
        await state.set_state(Booking.time)
        text, kb = await times_view(db, config, data)
        await callback.message.edit_text("😔 Пока вы оформляли запись, это время заняли.\n\n" + text, reply_markup=kb)
        return await callback.answer()

    await state.clear()
    booking = await db.get_booking(booking_id)
    reminder_note = "" if start - now <= timedelta(hours=REMIND_BEFORE_HOURS) else (
        f"Напомню за {REMIND_BEFORE_HOURS} ч до визита. "
    )
    await callback.message.edit_text(
        "✅ <b>Вы записаны!</b>\n\n"
        f"{fmt_booking(booking)}\n📍 {SALON_ADDRESS}\n\n"
        f"{reminder_note}Отменить запись можно в разделе «Мои записи»."
    )
    await callback.answer("Готово!")
    await notify_admins(bot, config, "🆕 <b>Новая запись</b>\n\n" + fmt_booking_admin(booking))


# ---------- Мои записи ----------

async def _my_bookings_view(db: Database, config: Config, user_id: int):
    bookings = await db.get_user_bookings(user_id, config.now())
    if not bookings:
        return f"У вас нет предстоящих записей. Нажмите «{BTN_BOOK}», чтобы записаться.", None
    text = "<b>Ваши записи:</b>\n\n" + "\n\n".join(fmt_booking(b) for b in bookings)
    return text, my_bookings_kb(bookings)


@router.message(F.text == BTN_MY)
async def my_bookings(message: Message, state: FSMContext, db: Database, config: Config):
    await state.clear()
    text, kb = await _my_bookings_view(db, config, message.from_user.id)
    await message.answer(text, reply_markup=kb or main_menu())


@router.callback_query(MyCB.filter(F.action == "list"))
async def my_bookings_list(callback: CallbackQuery, db: Database, config: Config):
    text, kb = await _my_bookings_view(db, config, callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(MyCB.filter(F.action == "ask"))
async def my_cancel_ask(callback: CallbackQuery, callback_data: MyCB, db: Database):
    booking = await db.get_booking(callback_data.id)
    if not booking or booking["user_id"] != callback.from_user.id or booking["status"] != "active":
        return await callback.answer("Запись не найдена или уже отменена", show_alert=True)
    await callback.message.edit_text(
        "Отменить эту запись?\n\n" + fmt_booking(booking),
        reply_markup=my_cancel_confirm_kb(booking["id"]),
    )
    await callback.answer()


@router.callback_query(MyCB.filter(F.action == "yes"))
async def my_cancel_yes(callback: CallbackQuery, callback_data: MyCB, db: Database, config: Config, bot: Bot):
    booking = await db.get_booking(callback_data.id)
    if (
        not booking
        or booking["user_id"] != callback.from_user.id
        or not await db.cancel_booking(booking["id"])
    ):
        return await callback.answer("Запись не найдена или уже отменена", show_alert=True)

    text, kb = await _my_bookings_view(db, config, callback.from_user.id)
    await callback.message.edit_text("Запись отменена.\n\n" + text, reply_markup=kb)
    await callback.answer("Запись отменена")
    await notify_admins(bot, config, "❌ <b>Клиент отменил запись</b>\n\n" + fmt_booking_admin(booking))


# ---------- Всё остальное ----------

@router.callback_query()
async def stale_button(callback: CallbackQuery):
    await callback.answer(f"Эта кнопка устарела. Начните заново: «{BTN_BOOK}»", show_alert=True)


@router.message()
async def unknown_message(message: Message):
    await message.answer("Не понял 🙂 Воспользуйтесь кнопками меню.", reply_markup=main_menu())
