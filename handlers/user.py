"""Сценарий клиента: запись, мои записи, отзывы, информация о салоне."""
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import Config
from database import Database
from keyboards import (
    BTN_ABORT, BTN_ABOUT, BTN_BOOK, BTN_MY, MENU_BUTTONS,
    DayCB, MasterCB, MonthCB, MyCB, NavCB, NoopCB, ReviewCB, ServiceCB, TimeCB,
    calendar_kb, confirm_kb, main_menu, masters_kb, my_bookings_kb, my_cancel_confirm_kb,
    phone_kb, services_kb, skip_comment_kb, times_kb,
)
from settings import DT_FMT, REMIND_BEFORE_HOURS, SALON_ADDRESS, SALON_NAME, SALON_PHONE, WELCOME_IMAGE
from states import Booking, Review
from utils.formatting import (
    card, esc, fmt_booking, fmt_booking_admin, fmt_day_long, fmt_rating, fmt_work_hours,
    normalize_phone, plural, stars, step,
)
from utils.notify import notify_admins
from utils.schedule import free_slots, upcoming_days

router = Router(name="user")

# Текстовый ввод: не команда и не кнопка главного меню
TEXT_INPUT = (F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTONS))


# ---------- Экраны шагов записи ----------
# Каждая функция возвращает текст и клавиатуру, чтобы один и тот же экран
# можно было показать и при движении вперёд, и по кнопке «Назад».

def _context(service: dict, master: dict | None = None, day: date | None = None) -> str:
    """Карточка с тем, что клиент уже выбрал."""
    return card(
        f"<b>{esc(service['name'])}</b> · {service['price']} ₽",
        f"Мастер: {esc(master['name'])}" if master else "",
        fmt_day_long(day) if day else "",
    )


async def services_view(db: Database):
    services = await db.get_services()
    lines = [f"<b>Выберите услугу</b>\n{step(1)}\n"]
    for s in services:
        lines.append(f"<b>{esc(s['name'])}</b>\n{s['price']} ₽ · {s['duration']} мин")
        if s["description"]:
            lines.append(f"<i>{esc(s['description'])}</i>")
        lines.append("")
    return "\n".join(lines).rstrip(), services_kb(services)


async def masters_view(db: Database, data: dict):
    service = await db.get_service(data["service_id"])
    masters = await db.get_masters()
    lines = [_context(service), f"<b>Выберите мастера</b>\n{step(2)}\n"]
    for m in masters:
        lines.append(f"<b>{esc(m['name'])}</b> · {fmt_rating(m)}")
        if m["description"]:
            lines.append(f"<i>{esc(m['description'])}</i>")
        lines.append("")
    return "\n".join(lines).rstrip(), masters_kb(masters)


async def available_days(db: Database, config: Config, data: dict) -> list[date]:
    """Дни, в которых у мастера есть хотя бы одно окно под выбранную услугу."""
    service = await db.get_service(data["service_id"])
    now = config.now()
    days = []
    for d in upcoming_days(now.date()):
        busy = await db.get_busy_intervals(data["master_id"], d)
        if free_slots(d, service["duration"], busy, now):
            days.append(d)
    return days


async def days_view(db: Database, config: Config, data: dict, month: date | None = None):
    service = await db.get_service(data["service_id"])
    master = await db.get_master(data["master_id"])
    today = config.now().date()
    all_days = upcoming_days(today)
    days = await available_days(db, config, data)

    text = _context(service, master) + "\n"
    if not days:
        text += "У этого мастера нет свободных окон в ближайшие две недели. Выберите другого мастера."
    else:
        text += f"<b>Выберите день</b>\n{step(3)}\n\n<i>Числами отмечены дни со свободным временем.</i>"

    if month is None:
        month = days[0] if days else today
    first_day, last_day = today, (all_days[-1] if all_days else today)
    return text, calendar_kb(month, set(days), first_day, last_day)


async def times_view(db: Database, config: Config, data: dict):
    service = await db.get_service(data["service_id"])
    master = await db.get_master(data["master_id"])
    day = date.fromisoformat(data["day"])
    busy = await db.get_busy_intervals(data["master_id"], day)
    slots = free_slots(day, service["duration"], busy, config.now())
    text = _context(service, master, day) + "\n"
    if not slots:
        return text + "На этот день свободного времени не осталось.", times_kb([])
    return text + f"<b>Выберите время</b>\n{step(4)}", times_kb(slots)


async def summary_text(db: Database, data: dict) -> str:
    service = await db.get_service(data["service_id"])
    master = await db.get_master(data["master_id"])
    start = datetime.strptime(data["start_at"], DT_FMT)
    end = start + timedelta(minutes=service["duration"])
    return (
        "<b>Проверьте запись</b>\n"
        + card(
            f"<b>{fmt_day_long(start.date())}, {start:%H:%M}–{end:%H:%M}</b>",
            esc(service["name"]),
            f"Мастер: {esc(master['name'])}",
            f"Стоимость: {service['price']} ₽, оплата в салоне",
        )
        + f"\n{esc(data['client_name'])}, {data['phone']}"
    )


# ---------- Старт и главное меню ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database):
    await state.clear()
    user = message.from_user
    await db.add_user(user.id, user.username, user.first_name)
    text = (
        f"Здравствуйте, {esc(user.first_name or 'гость')}!\n\n"
        f"Это бот <b>{SALON_NAME}</b>. Здесь можно записаться на стрижку "
        "за минуту — без звонков и ожидания ответа.\n\n"
        + card(SALON_ADDRESS, fmt_work_hours())
        + f"\nЧтобы выбрать время, нажмите «{BTN_BOOK}» в меню внизу."
    )
    if WELCOME_IMAGE.exists():
        await message.answer_photo(FSInputFile(WELCOME_IMAGE), caption=text, reply_markup=main_menu())
    else:
        await message.answer(text, reply_markup=main_menu())


@router.message(Command("cancel"))
@router.message(F.text == BTN_ABORT)
async def abort_by_text(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu())


@router.callback_query(NavCB.filter(F.to == "abort"))
async def abort_by_button(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Запись отменена. Возвращайтесь, когда будет удобно.")
    await callback.answer()


@router.callback_query(NoopCB.filter())
async def noop(callback: CallbackQuery):
    """Кнопки-подписи в календаре и сетке времени ничего не делают."""
    await callback.answer()


@router.message(F.text == BTN_ABOUT)
async def about(message: Message, db: Database):
    services = await db.get_services()
    masters = await db.get_masters()
    prices = "\n".join(f"{esc(s['name'])} — <b>{s['price']} ₽</b>" for s in services)
    team = "\n".join(f"{esc(m['name'])} · {fmt_rating(m)}" for m in masters)
    await message.answer(
        f"<b>{SALON_NAME}</b>\n"
        + card(SALON_ADDRESS, fmt_work_hours(), SALON_PHONE)
        + f"\n<b>Цены</b>\n{prices}\n\n<b>Мастера</b>\n{team}",
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
    service = await db.get_service(callback_data.id)
    if not service or not service["is_active"]:
        return await callback.answer("Эта услуга сейчас недоступна", show_alert=True)
    await state.update_data(service_id=service["id"])
    await state.set_state(Booking.master)
    text, kb = await masters_view(db, await state.get_data())
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(Booking.master, MasterCB.filter())
async def pick_master(
    callback: CallbackQuery, callback_data: MasterCB, state: FSMContext, db: Database, config: Config
):
    master = await db.get_master(callback_data.id)
    if not master or not master["is_active"]:
        return await callback.answer("Этот мастер сейчас не принимает", show_alert=True)
    await state.update_data(master_id=master["id"])
    await state.set_state(Booking.day)
    text, kb = await days_view(db, config, await state.get_data())
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(Booking.day, MonthCB.filter())
async def change_month(callback: CallbackQuery, callback_data: MonthCB, state: FSMContext, db: Database, config: Config):
    month = datetime.strptime(callback_data.value, "%Y%m").date()
    text, kb = await days_view(db, config, await state.get_data(), month)
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
        await callback.message.edit_text("<b>Это время уже недоступно.</b> Выберите другое.\n\n" + text, reply_markup=kb)
        return await callback.answer()

    await state.update_data(start_at=start.strftime(DT_FMT))
    await state.set_state(Booking.name)
    await callback.message.edit_text(
        _context(service, await db.get_master(data["master_id"]))
        + f"\n<b>{fmt_day_long(start.date())}, {start:%H:%M}</b> — отлично.\n\n"
        "Как к вам обращаться? Напишите имя."
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
        text, kb = await days_view(db, config, data, date.fromisoformat(data["day"]) if "day" in data else None)
    else:
        await state.set_state(Booking.time)
        text, kb = await times_view(db, config, data)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---------- Контактные данные ----------

@router.message(Booking.name, *TEXT_INPUT)
async def enter_name(message: Message, state: FSMContext):
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 50:
        return await message.answer("Имя должно быть от 2 до 50 символов. Попробуйте ещё раз.")
    await state.update_data(client_name=name)
    await state.set_state(Booking.phone)
    await message.answer(
        f"Приятно познакомиться, {esc(name)}.\n\n"
        "Оставьте номер телефона — мастер свяжется, если что-то изменится. "
        "Нажмите кнопку ниже или напишите номер вручную.",
        reply_markup=phone_kb(),
    )


@router.message(Booking.phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext, db: Database):
    phone = normalize_phone(message.contact.phone_number) or message.contact.phone_number
    await _save_phone(message, state, db, phone)


@router.message(Booking.phone, *TEXT_INPUT)
async def enter_phone_text(message: Message, state: FSMContext, db: Database):
    phone = normalize_phone(message.text)
    if not phone:
        return await message.answer("Не похоже на номер телефона. Пример: <code>+7 999 123-45-67</code>")
    await _save_phone(message, state, db, phone)


async def _save_phone(message: Message, state: FSMContext, db: Database, phone: str):
    await state.update_data(phone=phone)
    await state.set_state(Booking.confirm)
    # Убираем клавиатуру с кнопкой «Отправить номер», возвращаем главное меню
    await message.answer("Почти готово.", reply_markup=main_menu())
    await message.answer(await summary_text(db, await state.get_data()), reply_markup=confirm_kb())


# ---------- Подтверждение ----------

@router.callback_query(Booking.confirm, NavCB.filter(F.to == "confirm"))
async def confirm_booking(callback: CallbackQuery, state: FSMContext, db: Database, config: Config, bot: Bot):
    data = await state.get_data()
    service = await db.get_service(data["service_id"])
    start = datetime.strptime(data["start_at"], DT_FMT)
    now = config.now()
    soon = start - now <= timedelta(hours=REMIND_BEFORE_HOURS)

    booking_id = None
    if start > now:
        # Если до визита меньше, чем время напоминания, напоминать уже не нужно
        booking_id = await db.create_booking(
            user_id=callback.from_user.id,
            client_name=data["client_name"],
            phone=data["phone"],
            service_id=service["id"],
            master_id=data["master_id"],
            start=start,
            duration=service["duration"],
            price=service["price"],
            reminded=soon,
        )

    if booking_id is None:
        await state.set_state(Booking.time)
        text, kb = await times_view(db, config, data)
        await callback.message.edit_text(
            "<b>Пока вы оформляли запись, это время заняли.</b> Выберите другое.\n\n" + text, reply_markup=kb
        )
        return await callback.answer()

    await state.clear()
    booking = await db.get_booking(booking_id)
    hours = plural(REMIND_BEFORE_HOURS, "час", "часа", "часов")
    reminder_note = "" if soon else f"Напомню о визите за {hours}. "
    await callback.message.edit_text(
        "<b>Вы записаны</b>\n"
        + fmt_booking(booking)
        + f"\nЖдём вас по адресу: {SALON_ADDRESS}.\n\n"
        f"<i>{reminder_note}Отменить запись можно в разделе «{BTN_MY}».</i>"
    )
    await callback.answer("Готово!")
    await notify_admins(bot, config, "<b>Новая запись</b>\n" + fmt_booking_admin(booking))


# ---------- Мои записи ----------

async def _my_bookings_view(db: Database, config: Config, user_id: int):
    bookings = await db.get_user_bookings(user_id, config.now())
    if not bookings:
        return f"У вас нет предстоящих записей. Нажмите «{BTN_BOOK}», чтобы записаться.", None
    text = "<b>Ваши записи</b>\n" + "".join(fmt_booking(b) for b in bookings)
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
        "<b>Отменить эту запись?</b>\n" + fmt_booking(booking),
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
    await notify_admins(bot, config, "<b>Клиент отменил запись</b>\n" + fmt_booking_admin(booking))


# ---------- Отзывы ----------

@router.callback_query(ReviewCB.filter(F.rating.in_({1, 2, 3, 4, 5})))
async def rate_visit(callback: CallbackQuery, callback_data: ReviewCB, state: FSMContext,
                     db: Database, config: Config, bot: Bot):
    booking = await db.get_booking(callback_data.booking_id)
    if not booking or booking["user_id"] != callback.from_user.id:
        return await callback.answer("Визит не найден", show_alert=True)

    await db.save_rating(booking, callback_data.rating)
    await state.set_state(Review.comment)
    await state.update_data(review_booking_id=booking["id"])

    await callback.message.edit_text(
        f"Ваша оценка: <b>{stars(callback_data.rating)}</b>\nСпасибо!\n\n"
        "Если хотите, добавьте пару слов — напишите комментарий одним сообщением. "
        "Мы читаем каждый.",
        reply_markup=skip_comment_kb(booking["id"]),
    )
    await callback.answer()
    await notify_admins(
        bot, config,
        f"<b>Новая оценка: {stars(callback_data.rating)}</b>\n" + fmt_booking_admin(booking),
    )


@router.callback_query(ReviewCB.filter(F.rating == 0))
async def skip_comment(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Спасибо за оценку! Будем рады видеть вас снова.")
    await callback.answer()


@router.message(Review.comment, *TEXT_INPUT)
async def leave_comment(message: Message, state: FSMContext, db: Database, config: Config, bot: Bot):
    booking_id = (await state.get_data())["review_booking_id"]
    comment = message.text.strip()[:1000]
    await db.save_review_comment(booking_id, comment)
    await state.clear()
    await message.answer("Спасибо за отзыв! Будем рады видеть вас снова.", reply_markup=main_menu())

    booking = await db.get_booking(booking_id)
    await notify_admins(
        bot, config,
        f"<b>Отзыв о мастере {esc(booking['master_name'])}</b> · {stars(booking['rating'])}\n"
        + card(f"«{esc(comment)}»", f"— {esc(booking['client_name'])}"),
    )


# ---------- Всё остальное ----------

@router.callback_query()
async def stale_button(callback: CallbackQuery):
    await callback.answer(f"Эта кнопка устарела. Начните заново: «{BTN_BOOK}»", show_alert=True)


@router.message()
async def unknown_message(message: Message):
    await message.answer("Не понял. Воспользуйтесь кнопками меню внизу.", reply_markup=main_menu())
