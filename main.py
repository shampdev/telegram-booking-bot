"""Точка входа: python main.py"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat

from config import load_config
from database import Database
from filters import IsAdmin
from handlers import admin, user
from utils.reminders import reminder_loop

logger = logging.getLogger(__name__)


async def set_commands(bot: Bot, admin_ids: frozenset[int]) -> None:
    user_commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ]
    await bot.set_my_commands(user_commands)
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(
                user_commands + [BotCommand(command="admin", description="Админ-панель")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except TelegramAPIError:
            logger.warning("Админ %s ещё не запускал бота — команды для него не установлены", admin_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    config = load_config()

    db = Database(config.db_path)
    await db.connect()

    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    # Эти объекты будут автоматически передаваться в обработчики по имени аргумента
    dp["db"] = db
    dp["config"] = config

    admin.router.message.filter(IsAdmin(config.admin_ids))
    admin.router.callback_query.filter(IsAdmin(config.admin_ids))
    dp.include_routers(admin.router, user.router)  # порядок важен: в user есть «ловушка» для всего остального

    reminders = asyncio.create_task(reminder_loop(bot, db, config))
    try:
        await set_commands(bot, config.admin_ids)
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот запущен")
        await dp.start_polling(bot)
    finally:
        reminders.cancel()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
