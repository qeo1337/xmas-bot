import os
import datetime
import zoneinfo
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env_date(name: str, default: str) -> datetime.date:
    raw = os.getenv(name, default)
    return datetime.date.fromisoformat(raw)


class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}

    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "db")
    POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
    POSTGRES_USER = os.getenv("POSTGRES_USER", "xmas")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
    POSTGRES_DB = os.getenv("POSTGRES_DB", "xmas")

    TZ = zoneinfo.ZoneInfo(os.getenv("TZ_NAME", "Europe/Moscow"))
    DAY_WINDOW_START = int(os.getenv("DAY_WINDOW_START", "8"))
    DAY_WINDOW_END = int(os.getenv("DAY_WINDOW_END", "23"))
    DECOR_COOLDOWN_HOURS = int(os.getenv("DECOR_COOLDOWN_HOURS", os.getenv("WATER_COOLDOWN_HOURS", "3")))
    # Тестовая настройка: если задана, перекрывает часы (в минутах). Прод: не задавать.
    DECOR_COOLDOWN_MINUTES = int(os.getenv("DECOR_COOLDOWN_MINUTES", "0"))
    SNOWBALLS_PER_DAY = int(os.getenv("SNOWBALLS_PER_DAY", "3"))

    REGISTRATION_START = _env_date("REGISTRATION_START", f"{datetime.date.today().year}-11-27")
    REGISTRATION_END = _env_date("REGISTRATION_END", f"{datetime.date.today().year}-12-02")
    EVENT_START = _env_date("EVENT_START", f"{datetime.date.today().year}-12-01")
    EVENT_END = _env_date("EVENT_END", f"{datetime.date.today().year}-12-31")

    # Бонус серии "Садовода", см: при серии N дней берётся максимальный ключ <= N
    STREAK_BONUS = {1: 0, 2: 1, 3: 2, 5: 3, 7: 5, 14: 7, 21: 10, 28: 12}

    # Whitelist загружается из текстового файла, одна строка = username без @
    WHITELIST_FILE = Path(os.getenv("WHITELIST_FILE", "whitelist.txt"))

    @staticmethod
    def streak_bonus(streak: int) -> int:
        bonus = 0
        for days, value in Config.STREAK_BONUS.items():
            if streak >= days:
                bonus = value
        return bonus


def now_local() -> datetime.datetime:
    return datetime.datetime.now(Config.TZ)


def streak_bonus_for(streak: int) -> int:
    return Config.streak_bonus(streak)
