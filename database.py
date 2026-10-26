import datetime
import os

import asyncpg

from config import Config

_pool: asyncpg.Pool | None = None


async def init_db() -> asyncpg.Pool:
    global _pool

    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=Config.POSTGRES_HOST,
            port=Config.POSTGRES_PORT,
            user=Config.POSTGRES_USER,
            password=Config.POSTGRES_PASSWORD,
            database=Config.POSTGRES_DB,
            min_size=1,
            max_size=10,
        )

        schema_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "db_init.sql",
        )

        async with _pool.acquire() as conn:
            with open(schema_path, encoding="utf-8") as f:
                await conn.execute(f.read())

    return _pool


def pool() -> asyncpg.Pool:
    assert _pool is not None, "init_db() must be called first"
    return _pool


async def load_whitelist() -> set[str]:
    """Загружает whitelist из файла (если есть) в БД (idempotent).
    Файл задаётся в WHITELIST_FILE (по умолчанию whitelist_tg.txt)."""
    if not Config.WHITELIST_FILE.exists():
        return set()
    rows = []
    with open(Config.WHITELIST_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:   # формат: логин|Имя Фамилия
                name, fio = line.split("|", 1)
                rows.append((name.strip().lstrip("@").lower(), fio.strip()))
            else:
                rows.append((line.lstrip("@").lower(), None))
    if rows:
        async with pool().acquire() as conn:
            await conn.executemany(
                """INSERT INTO whitelist (username, display_name) VALUES ($1, $2)
                   ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name""",
                rows,
            )
    return {r[0] for r in rows}


def registration_state(now: datetime.date) -> str:
    """before | open | closed"""
    if now < Config.REGISTRATION_START:
        return "before"
    if now <= Config.REGISTRATION_END:
        return "open"
    return "closed"


async def whitelist_display_name(username: str | None) -> str | None:
    """ФИО из whitelist (формат 'логин|Имя Фамилия'), если задано."""
    if not username:
        return None
    async with pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT display_name FROM whitelist WHERE username = $1",
            username.strip().lstrip("@").lower())


async def is_whitelisted(username: str | None) -> bool:
    if not username:
        return False
    async with pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM whitelist WHERE username = $1)",
            username.strip().lstrip("@").lower(),
        )


async def get_user(uid: int):
    async with pool().acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", uid)


async def register_user(uid: int, username: str | None, display_name: str | None) -> None:
    now = now_local_date = datetime.datetime.now(Config.TZ)
    async with pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO users (tg_id, username, display_name, snowballs_left, snowballs_day)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (tg_id) DO NOTHING""",
            uid, username, display_name,
            0 if now_local_date.date() < Config.EVENT_START else Config.SNOWBALLS_PER_DAY,
            now_local_date.date())


# ------------------------------------------------------------- логирование
# Для организаторов: каждое действие пишется в event_log (кто, что, когда) —
# полный журнал разбирается админ-командами без открытия БД.

async def log_event(uid: int, username: str, action: str, details: str = ""):
    async with pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO event_log (tg_id, username, action, details) VALUES ($1, $2, $3, $4)",
            uid, username, action, details)
