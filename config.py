"""Загрузка секретов и окружения из .env."""
import os
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    db_path: str
    timezone: str

    def now(self) -> datetime:
        """Текущее время салона (без tzinfo, с точностью до минуты)."""
        return datetime.now(ZoneInfo(self.timezone)).replace(tzinfo=None, second=0, microsecond=0)


def load_config() -> Config:
    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен от @BotFather.")

    raw_admins = os.getenv("ADMIN_IDS", "")
    try:
        admin_ids = frozenset(int(x) for x in raw_admins.replace(" ", "").split(",") if x)
    except ValueError as e:
        raise RuntimeError("ADMIN_IDS должен быть списком числовых id через запятую") from e

    timezone = os.getenv("TIMEZONE", "Europe/Moscow")
    ZoneInfo(timezone)  # упадём сразу, если часовой пояс указан с ошибкой

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        db_path=os.getenv("DB_PATH", "bot.db"),
        timezone=timezone,
    )
