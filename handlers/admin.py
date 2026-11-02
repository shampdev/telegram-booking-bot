"""Админ-панель: записи по дням, отмена, рассылка.
Фильтр IsAdmin вешается на этот роутер в main.py."""
import asyncio
import html
import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import Config
from database import Database
from keyboards import (
    AdminCancelCB, AdminCB,
    admin_day_kb, admin_days_kb, admin_menu_kb, broadcast_confirm_kb,
)
from settings import SALON_PHONE
from states import Broadcast
from utils.formatting import fmt_booking, fmt_day_long, parse_dt

router = Router(name="admin")
logger = logging.getLogger(__name__)

MENU_TEXT = "🛠 <b>Админ-панель</b>\n\nВыберите действие:"


async def day_view(db: Database, day_value: str):
    day = datetime.strptime(day_value, "%Y%m%d").date()
    bookings = await db.get_bookings_by_date(day)
    if not bookings:
        return f"📅 <b>{fmt_day_long(day)}</b>\n\nЗаписей нет.", admin_day_kb([], day_value)

    lines = [f"📅 <b>{fmt_day_long(day)}</b> — записей: {len(bookings)}\n"]
    for b in bookings:
        start, end = parse_dt(b["start_at"]), parse_dt(b["end_at"])
        lines.append(
            f"<b>{start:%H:%M}–{end:%H:%M}</b> · {html.escape(b['master_name'])}\n"
            f"{html.escape(b['service_name'])}\n"
            f"🙋 {html.escape(b['client_name'])}, {html.escape(b['phone'])}\n"
        )
    lines.append("Нажмите на запись ниже, чтобы отменить её.")
    return "\n".join(lines), admin_day_kb(bookings, day_value)


@router.message(Command("admin"))
async def admin_menu(message: Message, state: FSMContext, config: Config):
    await state.clear()
    await message.answer(MENU_TEXT, reply_markup=admin_menu_kb(config.now().date()))


@router.callback_query(AdminCB.filter(F.action == "menu"))
async def admin_menu_cb(callback: CallbackQuery, state: FSMContext, config: Config):
    await state.clear()
    await callback.message.edit_text(MENU_TEXT, reply_markup=admin_menu_kb(config.now().date()))
    await callback.answer()


@router.callback_query(AdminCB.filter(F.action == "pick"))
async def admin_pick_day(callback: CallbackQuery, config: Config):
    await callback.message.edit_text("Выберите дату:", reply_markup=admin_days_kb(config.now().date()))
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
                "😔 <b>Ваша запись отменена салоном</b>\n\n"
                f"{fmt_booking(booking)}\n\n"
                f"Приносим извинения! Выберите другое время или позвоните нам: {SALON_PHONE}",
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
        "📣 Отправьте текст рассылки одним сообщением.\n"
        "Его получат все, кто запускал бота. Для отмены — /cancel"
    )
    await callback.answer()


@router.message(Broadcast.text, F.text, ~F.text.startswith("/"))
async def broadcast_preview(message: Message, state: FSMContext, db: Database):
    await state.update_data(text=message.html_text)
    await state.set_state(Broadcast.confirm)
    count = len(await db.get_user_ids())
    await message.answer(
        f"Так будет выглядеть сообщение (получателей: {count}):\n\n{message.html_text}",
        reply_markup=broadcast_confirm_kb(),
    )


@router.callback_query(Broadcast.confirm, AdminCB.filter(F.action == "bc_send"))
async def broadcast_send(callback: CallbackQuery, state: FSMContext, db: Database, bot: Bot, config: Config):
    text = (await state.get_data())["text"]
    await state.clear()
    await callback.message.edit_text("⏳ Отправляю…")
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
        f"✅ Рассылка завершена.\nДоставлено: {sent}\nНе доставлено: {failed}",
        reply_markup=admin_menu_kb(config.now().date()),
    )
