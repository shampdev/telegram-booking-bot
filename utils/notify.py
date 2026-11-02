import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import Config

logger = logging.getLogger(__name__)


async def notify_admins(bot: Bot, config: Config, text: str) -> None:
    for admin_id in config.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError as e:
            # Админ мог не нажать /start в боте — тогда писать ему нельзя
            logger.warning("Не удалось уведомить админа %s: %s", admin_id, e)
