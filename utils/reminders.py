"""Фоновая задача: напоминает о визите и просит оценку после него."""
import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import Config
from database import Database
from keyboards import rating_kb
from settings import REMIND_BEFORE_HOURS, REVIEW_AFTER_MIN, SALON_ADDRESS
from utils.formatting import esc, fmt_booking

logger = logging.getLogger(__name__)


async def send_reminders(bot: Bot, db: Database, config: Config) -> None:
    now = config.now()
    for booking in await db.get_due_reminders(now, now + timedelta(hours=REMIND_BEFORE_HOURS)):
        try:
            await bot.send_message(
                booking["user_id"],
                "<b>Напоминаем о записи</b>\n"
                + fmt_booking(booking)
                + f"\nЖдём вас по адресу: {SALON_ADDRESS}.\n\n"
                "<i>Если планы изменились, отмените запись в разделе «Мои записи».</i>",
            )
        except TelegramAPIError as e:
            logger.warning("Не удалось отправить напоминание %s: %s", booking["id"], e)
        await db.mark_reminded(booking["id"])


async def send_review_requests(bot: Bot, db: Database, config: Config) -> None:
    ended_before = config.now() - timedelta(minutes=REVIEW_AFTER_MIN)
    for booking in await db.get_due_review_requests(ended_before, ended_before - timedelta(days=1)):
        try:
            await bot.send_message(
                booking["user_id"],
                f"Спасибо, что выбрали нас, {esc(booking['client_name'])}!\n\n"
                f"Как вам визит к мастеру <b>{esc(booking['master_name'])}</b>? "
                "Оцените, пожалуйста, от 1 до 5 — это займёт секунду.",
                reply_markup=rating_kb(booking["id"]),
            )
        except TelegramAPIError as e:
            logger.warning("Не удалось запросить отзыв %s: %s", booking["id"], e)
        await db.mark_review_requested(booking["id"])


async def background_loop(bot: Bot, db: Database, config: Config, interval: int = 60) -> None:
    while True:
        try:
            await send_reminders(bot, db, config)
            await send_review_requests(bot, db, config)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в фоновой задаче")
        await asyncio.sleep(interval)
