"""Админ-панель: расписание, рассылка, услуги, мастера, отзывы, выгрузка в Excel.
Фильтр IsAdmin вешается на этот роутер в main.py."""
import asyncio
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import Config
from database import Database
from keyboards import (
    MENU_BUTTONS, AdminCancelCB, AdminCB, AdminItemCB,
    admin_back_kb, admin_cancel_input_kb, admin_day_kb, admin_days_kb, admin_export_kb,
    admin_item_kb, admin_list_kb, admin_menu_kb, admin_skip_kb, broadcast_confirm_kb,
)
from settings import SALON_PHONE
from states import AdminForm, Broadcast
from utils.export import build_bookings_xlsx
from utils.formatting import card, esc, fmt_booking, fmt_day_long, fmt_rating, parse_dt, plural, stars

router = Router(name="admin")
logger = logging.getLogger(__name__)

MENU_TEXT = "<b>Админ-панель</b>\nВыберите раздел:"

# Текстовый ввод в админке: не команда и не кнопка главного меню
TEXT_INPUT = (F.text, ~F.text.startswith("/"), ~F.text.in_(MENU_BUTTONS))


async def show_menu(target: Message, config: Config, edit: bool) -> None:
    kb = admin_menu_kb(config.now().date())
    if edit:
        await target.edit_text(MENU_TEXT, reply_markup=kb)
    else:
        await target.answer(MENU_TEXT, reply_markup=kb)


@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext, config: Config):
    await state.clear()
    await show_menu(message, config, edit=False)


@router.callback_query(AdminCB.filter(F.action == "menu"))
async def admin_menu_cb(callback: CallbackQuery, state: FSMContext, config: Config):
    await state.clear()
    await show_menu(callback.message, config, edit=True)
    await callback.answer()


# ---------- Расписание ----------

async def day_view(db: Database, day_value: str):
    day = datetime.strptime(day_value, "%Y%m%d").date()
    bookings = await db.get_bookings_by_date(day)
    if not bookings:
        return f"<b>{fmt_day_long(day)}</b>\nЗаписей нет.", admin_day_kb([], day_value)

    revenue = sum(b["price"] for b in bookings)
    parts = [f"<b>{fmt_day_long(day)}</b>\n<i>{plural(len(bookings), 'запись', 'записи', 'записей')} · {revenue} ₽</i>\n"]
    for b in bookings:
        start, end = parse_dt(b["start_at"]), parse_dt(b["end_at"])
        parts.append(card(
            f"<b>{start:%H:%M}–{end:%H:%M}</b> · {esc(b['master_name'])}",
            esc(b["service_name"]),
            f"{esc(b['client_name'])}, {esc(b['phone'])}",
        ))
    parts.append("\n<i>Чтобы отменить запись, нажмите на неё ниже.</i>")
    return "".join(parts), admin_day_kb(bookings, day_value)


@router.callback_query(AdminCB.filter(F.action == "pick"))
async def admin_pick_day(callback: CallbackQuery, config: Config):
    await callback.message.edit_text("<b>Выберите дату</b>", reply_markup=admin_days_kb(config.now().date()))
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "day"))
async def admin_day(callback: CallbackQuery, callback_data: AdminCB, db: Database):
    text, kb = await day_view(db, callback_data.value)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(AdminCancelCB.filter())
async def admin_cancel(callback: CallbackQuery, callback_data: AdminCancelCB, db: Database, bot: Bot):
    booking = await db.get_booking(callback_data.id)
    if not booking or not await db.cancel_booking(booking["id"]):
        await callback.answer("Запись уже отменена", show_alert=True)
    else:
        await callback.answer("Запись отменена, клиент уведомлён")
        try:
            await bot.send_message(
                booking["user_id"],
                "<b>Ваша запись отменена салоном</b>\n"
                + fmt_booking(booking)
                + f"\nПриносим извинения! Выберите другое время или позвоните нам: {SALON_PHONE}",
            )
        except TelegramAPIError as e:
            logger.warning("Не удалось уведомить клиента %s: %s", booking["user_id"], e)

    text, kb = await day_view(db, callback_data.day)
    await callback.message.edit_text(text, reply_markup=kb)


# ---------- Рассылка ----------

@router.callback_query(AdminCB.filter(F.action == "broadcast"))
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.text)
    await callback.message.edit_text(
        "<b>Рассылка</b>\nОтправьте текст одним сообщением. "
        "Его получат все, кто запускал бота.",
        reply_markup=admin_cancel_input_kb("menu"),
    )
    await callback.answer()


@router.message(Broadcast.text, *TEXT_INPUT)
async def broadcast_preview(message: Message, state: FSMContext, db: Database):
    await state.update_data(text=message.html_text)
    await state.set_state(Broadcast.confirm)
    count = len(await db.get_user_ids())
    await message.answer(
        f"<b>Предпросмотр</b> · получателей: {count}\n\n{message.html_text}",
        reply_markup=broadcast_confirm_kb(),
    )


@router.callback_query(Broadcast.confirm, AdminCB.filter(F.action == "bc_send"))
async def broadcast_send(callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot, config: Config):
    text = (await state.get_data())["text"]
    await state.clear()
    await callback.message.edit_text("Отправляю…")
    await callback.answer()

    sent = failed = 0
    for user_id in await db.get_user_ids():
        try:
            await bot.send_message(user_id, text)
            sent += 1
        except TelegramAPIError:
            failed += 1  # пользователь заблокировал бота
        await asyncio.sleep(0.05)  # не упираемся в лимиты Telegram

    await callback.message.edit_text(
        f"<b>Рассылка завершена</b>\nДоставлено: {sent}\nНе доставлено: {failed}",
        reply_markup=admin_menu_kb(config.now().date()),
    )


# ---------- Услуги ----------

async def services_list_view(db: Database):
    services = await db.get_services(include_hidden=True)
    text = "<b>Услуги</b>\n<i>Нажмите на услугу, чтобы изменить цену или скрыть её от клиентов.</i>"
    return text, admin_list_kb("srv", services, "+ Добавить услугу")


def service_card(s: dict) -> str:
    status = "показывается клиентам" if s["is_active"] else "скрыта от клиентов"
    return f"<b>{esc(s['name'])}</b>\n" + card(
        f"<i>{esc(s['description'])}</i>" if s["description"] else "",
        f"Цена: <b>{s['price']} ₽</b>",
        f"Длительность: {s['duration']} мин",
        f"Статус: {status}",
    )


@router.callback_query(AdminCB.filter(F.action == "services"))
async def admin_services(callback: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    text, kb = await services_list_view(db)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(AdminItemCB.filter(F.kind == "srv"))
async def admin_service_action(callback: CallbackQuery, callback_data: AdminItemCB, state: FSMContext, db: Database):
    service = await db.get_service(callback_data.id)
    if not service:
        return await callback.answer("Услуга не найдена", show_alert=True)

    if callback_data.action == "price":
        await state.set_state(AdminForm.service_new_price)
        await state.update_data(service_id=service["id"])
        await callback.message.edit_text(
            f"<b>Новая цена</b> для «{esc(service['name'])}»\n"
            f"Сейчас: {service['price']} ₽. Отправьте число в рублях.",
            reply_markup=admin_cancel_input_kb("services"),
        )
        return await callback.answer()

    if callback_data.action == "toggle":
        await db.toggle_service(service["id"])
        service = await db.get_service(service["id"])
        await callback.answer("Услуга скрыта" if not service["is_active"] else "Услуга снова доступна")
    else:
        await callback.answer()
    await callback.message.edit_text(service_card(service), reply_markup=admin_item_kb("srv", service))


@router.message(AdminForm.service_new_price, *TEXT_INPUT)
async def admin_service_new_price(message: Message, state: FSMContext, db: Database):
    price = _parse_int(message.text, 0, 1_000_000)
    if price is None:
        return await message.answer("Нужно целое число, например <code>1500</code>")
    service_id = (await state.get_data())["service_id"]
    await db.set_service_price(service_id, price)
    await state.clear()
    service = await db.get_service(service_id)
    await message.answer("Цена обновлена.\n\n" + service_card(service), reply_markup=admin_item_kb("srv", service))


# Добавление услуги: название → цена → длительность → описание

@router.callback_query(AdminCB.filter(F.action == "add_srv"))
async def admin_add_service(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminForm.service_name)
    await callback.message.edit_text(
        "<b>Новая услуга</b>\n<i>Шаг 1 из 4</i>\n\nКак она называется?",
        reply_markup=admin_cancel_input_kb("services"),
    )
    await callback.answer()


@router.message(AdminForm.service_name, *TEXT_INPUT)
async def admin_add_service_name(message: Message, state: FSMContext):
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 40:
        return await message.answer("Название — от 2 до 40 символов.")
    await state.update_data(name=name)
    await state.set_state(AdminForm.service_price)
    await message.answer("<i>Шаг 2 из 4</i>\nЦена в рублях:", reply_markup=admin_cancel_input_kb("services"))


@router.message(AdminForm.service_price, *TEXT_INPUT)
async def admin_add_service_price(message: Message, state: FSMContext):
    price = _parse_int(message.text, 0, 1_000_000)
    if price is None:
        return await message.answer("Нужно целое число, например <code>1500</code>")
    await state.update_data(price=price)
    await state.set_state(AdminForm.service_duration)
    await message.answer(
        "<i>Шаг 3 из 4</i>\nДлительность в минутах, например <code>60</code>:",
        reply_markup=admin_cancel_input_kb("services"),
    )


@router.message(AdminForm.service_duration, *TEXT_INPUT)
async def admin_add_service_duration(message: Message, state: FSMContext):
    duration = _parse_int(message.text, 10, 600)
    if duration is None:
        return await message.answer("Нужно число минут от 10 до 600.")
    await state.update_data(duration=duration)
    await state.set_state(AdminForm.service_description)
    await message.answer(
        "<i>Шаг 4 из 4</i>\nКороткое описание — что входит в услугу:",
        reply_markup=admin_skip_kb("srv_nodesc", "services"),
    )


@router.message(AdminForm.service_description, *TEXT_INPUT)
async def admin_add_service_description(message: Message, state: FSMContext, db: Database):
    await _finish_service(message, state, db, message.text.strip()[:150])


@router.callback_query(AdminForm.service_description, AdminCB.filter(F.action == "srv_nodesc"))
async def admin_add_service_no_description(callback: CallbackQuery, state: FSMContext, db: Database):
    await callback.answer()
    await _finish_service(callback.message, state, db, "")


async def _finish_service(message: Message, state: FSMContext, db: Database, description: str):
    data = await state.get_data()
    await state.clear()
    service_id = await db.add_service(data["name"], data["price"], data["duration"], description)
    service = await db.get_service(service_id)
    await message.answer("Услуга добавлена.\n\n" + service_card(service), reply_markup=admin_item_kb("srv", service))


# ---------- Мастера ----------

def master_card(m: dict) -> str:
    status = "принимает клиентов" if m["is_active"] else "скрыт от клиентов"
    return f"<b>{esc(m['name'])}</b>\n" + card(
        f"<i>{esc(m['description'])}</i>" if m["description"] else "",
        f"Рейтинг: {fmt_rating(m)}",
        f"Статус: {status}",
    )


async def _master_with_rating(db: Database, master_id: int) -> dict | None:
    return next((m for m in await db.get_masters(include_hidden=True) if m["id"] == master_id), None)


@router.callback_query(AdminCB.filter(F.action == "masters"))
async def admin_masters(callback: CallbackQuery, state: FSMContext, db: Database):
    await state.clear()
    masters = await db.get_masters(include_hidden=True)
    await callback.message.edit_text(
        "<b>Мастера</b>\n<i>Скрытый мастер не показывается клиентам, "
        "но его текущие записи сохраняются.</i>",
        reply_markup=admin_list_kb("mst", masters, "+ Добавить мастера"),
    )
    await callback.answer()


@router.callback_query(AdminItemCB.filter(F.kind == "mst"))
async def admin_master_action(callback: CallbackQuery, callback_data: AdminItemCB, db: Database):
    master = await _master_with_rating(db, callback_data.id)
    if not master:
        return await callback.answer("Мастер не найден", show_alert=True)
    if callback_data.action == "toggle":
        await db.toggle_master(master["id"])
        master = await _master_with_rating(db, master["id"])
        await callback.answer("Мастер скрыт" if not master["is_active"] else "Мастер снова принимает")
    else:
        await callback.answer()
    await callback.message.edit_text(master_card(master), reply_markup=admin_item_kb("mst", master))


@router.callback_query(AdminCB.filter(F.action == "add_mst"))
async def admin_add_master(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminForm.master_name)
    await callback.message.edit_text(
        "<b>Новый мастер</b>\n<i>Шаг 1 из 2</i>\n\nИмя мастера:",
        reply_markup=admin_cancel_input_kb("masters"),
    )
    await callback.answer()


@router.message(AdminForm.master_name, *TEXT_INPUT)
async def admin_add_master_name(message: Message, state: FSMContext):
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 30:
        return await message.answer("Имя — от 2 до 30 символов.")
    await state.update_data(name=name)
    await state.set_state(AdminForm.master_description)
    await message.answer(
        "<i>Шаг 2 из 2</i>\nПара слов о мастере — стаж, специализация:",
        reply_markup=admin_skip_kb("mst_nodesc", "masters"),
    )


@router.message(AdminForm.master_description, *TEXT_INPUT)
async def admin_add_master_description(message: Message, state: FSMContext, db: Database):
    await _finish_master(message, state, db, message.text.strip()[:150])


@router.callback_query(AdminForm.master_description, AdminCB.filter(F.action == "mst_nodesc"))
async def admin_add_master_no_description(callback: CallbackQuery, state: FSMContext, db: Database):
    await callback.answer()
    await _finish_master(callback.message, state, db, "")


async def _finish_master(message: Message, state: FSMContext, db: Database, description: str):
    name = (await state.get_data())["name"]
    await state.clear()
    master = await _master_with_rating(db, await db.add_master(name, description))
    await message.answer("Мастер добавлен.\n\n" + master_card(master), reply_markup=admin_item_kb("mst", master))


# ---------- Отзывы ----------

@router.callback_query(AdminCB.filter(F.action == "reviews"))
async def admin_reviews(callback: CallbackQuery, db: Database):
    masters = await db.get_masters(include_hidden=True)
    ranking = "\n".join(
        f"{esc(m['name'])} · {fmt_rating(m)}"
        for m in sorted(masters, key=lambda m: (m["rating"] or 0, m["reviews_count"]), reverse=True)
    )
    parts = ["<b>Рейтинг мастеров</b>\n", card(ranking), "\n<b>Последние отзывы</b>\n"]

    reviews = await db.get_recent_reviews(5)
    if not reviews:
        parts.append("<i>Отзывов пока нет. Бот сам попросит оценку у клиентов после визита.</i>")
    for r in reviews:
        parts.append(card(
            f"{stars(r['rating'])} · {esc(r['master_name'])}",
            f"«{esc(r['comment'])}»" if r["comment"] else "",
            f"<i>— {esc(r['client_name'])}</i>",
        ))
    await callback.message.edit_text("".join(parts), reply_markup=admin_back_kb())
    await callback.answer()


# ---------- Выгрузка в Excel ----------

def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


@router.callback_query(AdminCB.filter(F.action == "export"))
async def admin_export(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>Выгрузка в Excel</b>\n<i>В файле будут все записи — с отменёнными и отзывами — "
        "и сводка по мастерам.</i>\n\nЗа какой период?",
        reply_markup=admin_export_kb(),
    )
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "xls"))
async def admin_export_file(callback: CallbackQuery, callback_data: AdminCB, db: Database, config: Config):
    now = config.now()
    this_month = _month_start(now.date())
    if callback_data.value == "this":
        start, end, label = this_month, _next_month(this_month), f"{this_month:%Y-%m}"
    elif callback_data.value == "prev":
        prev_month = _month_start(this_month - timedelta(days=1))
        start, end, label = prev_month, this_month, f"{prev_month:%Y-%m}"
    else:
        start, end, label = None, None, "all"

    bookings = await db.get_bookings_between(start, end)
    if not bookings:
        return await callback.answer("За этот период записей нет", show_alert=True)

    await callback.answer("Готовлю файл…")
    data = build_bookings_xlsx(bookings, now)
    await callback.message.answer_document(
        BufferedInputFile(data, filename=f"bookings_{label}.xlsx"),
        caption=f"Выгрузка: {plural(len(bookings), 'запись', 'записи', 'записей')}",
    )


# ---------- Вспомогательное ----------

def _parse_int(text: str, min_value: int, max_value: int) -> int | None:
    cleaned = text.replace(" ", "").replace("₽", "").replace("руб", "")
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    return value if min_value <= value <= max_value else None
