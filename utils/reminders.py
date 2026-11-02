"""Фоновая задача: напоминает клиентам о визите."""
import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import Config
from database import Database
from settings import REMIND_BEFORE_HOURS, SALON_ADDRESS
from utils.formatting import fmt_booking

logger = logging.getLogger(__name__)


async def reminder_loop(bot: Bot, db: Database, config: Config, interval: int = 60) -> None:
    while True:
        try:
            now = config.now()
            due = await db.get_due_reminders(now, now + timedelta(hours=REMIND_BEFORE_HOURS))
            for booking in due:
                try:
                    await bot.send_message(
                        booking["user_id"],
                        "⏰ <b>Напоминаем о записи</b>\n\n"
                        f"{fmt_booking(booking)}\n📍 {SALON_ADDRESS}\n\n"
                        "Если планы изменились, отмените запись в разделе «Мои записи».",
                    )
                except TelegramAPIError as e:
                    logger.warning("Не удалось отправить напоминание %s: %s", booking["id"], e)
                await db.mark_reminded(booking["id"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в цикле напоминаний")
        await asyncio.sleep(interval)
