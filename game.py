"""Игровая логика: украшения, серии, снежки, достижения, рейтинги."""

import datetime

import asyncpg

from config import Config, now_local
from database import pool


# ---------------------------------------------------------------- украшение

BALLS_PER_DECOR = 1  # за одно действие вешается 1 шар
BASE_GROWTH = 1       # базовое число шаров за украшение (без бонуса серии)


def next_decor_at(last: datetime.datetime) -> datetime.datetime:
    minutes = Config.DECOR_COOLDOWN_MINUTES
    delta = datetime.timedelta(minutes=minutes) if minutes > 0 else datetime.timedelta(hours=Config.DECOR_COOLDOWN_HOURS)
    return last + delta


def fmt_delta(seconds: int) -> str:
    m = seconds // 60
    h, m = divmod(m, 60)
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"


def in_day_window(now) -> bool:
    return Config.DAY_WINDOW_START <= now.hour <= Config.DAY_WINDOW_END


def _streak_update(u, now, day_active: bool):
    """Возвращает (new_streak, new_streak_last_day) по правилам календарной серии."""
    today = now.date()
    if not day_active:
        return u["streak"], u["streak_last_day"]
    if u["streak_last_day"] == today:
        return u["streak"], today
    if u["streak_last_day"] == today - datetime.timedelta(days=1):
        return u["streak"] + 1, today
    return 1, today


async def decorate(uid: int) -> dict:
    """Вешает шар на ёлку. Возвращает dict с результатом или ошибкой."""
    now = now_local()
    day_active = in_day_window(now)
    async with pool().acquire() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1 FOR UPDATE", uid)
        if not u:
            return {"ok": False, "error": "no_user"}
        if u["blocked"]:
            return {"ok": False, "error": "blocked"}
        if u["last_decor"] and now < next_decor_at(u["last_decor"]):
            wait = int((next_decor_at(u["last_decor"]) - now).total_seconds())
            return {"ok": False, "error": "cooldown", "wait": wait}

        new_streak, new_last_day = _streak_update(u, now, day_active)
        bonus = Config.streak_bonus(new_streak) if day_active else 0
        balls = BASE_GROWTH + bonus

        await conn.execute(
            """UPDATE users SET
                 ornaments = ornaments + $2,
                 last_decor = $3,
                 streak = $4,
                 best_streak = GREATEST(best_streak, $4),
                 streak_last_day = $5,
                 decor_total = decor_total + 1
               WHERE tg_id = $1""",
            uid, balls, now, new_streak, new_last_day,
        )
        return {
            "ok": True, "balls": balls, "bonus": bonus,
            "streak": new_streak, "best": max(u["best_streak"], new_streak),
            "ornaments": u["ornaments"] + balls,
            "total": u["decor_total"] + 1,
            "in_day_window": day_active,
        }


# ---------------------------------------------------------------- снежки

async def refill_snowballs_if_new_day(conn: asyncpg.Connection, u) -> None:
    today = now_local().date()
    if u["snowballs_day"] != today:
        await conn.execute(
            "UPDATE users SET snowballs_left = $2, snowballs_day = $3 WHERE tg_id = $1",
            u["tg_id"], Config.SNOWBALLS_PER_DAY, today,
        )
        u = dict(u) | {"snowballs_left": Config.SNOWBALLS_PER_DAY}


async def snowball_left(uid: int) -> int:
    async with pool().acquire() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", uid)
        if not u:
            return 0
        await refill_snowballs_if_new_day(conn, u)
        if u["snowballs_day"] != now_local().date():
            return Config.SNOWBALLS_PER_DAY
        return u["snowballs_left"]


async def snowball_from_pool(uid: int) -> int:
    """С учётом ленивой ежедневной выдачи."""
    left = await snowball_left(uid)
    if left > 0:
        return left
    async with pool().acquire() as conn:
        u = await conn.fetchrow(
            "SELECT snowballs_left FROM users WHERE tg_id = $1", uid)
        return u["snowballs_left"] if u else 0


async def throw_snowball(from_id: int, to_id: int) -> dict:
    async with pool().acquire() as conn:
        a = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1 FOR UPDATE", from_id)
        b = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1 FOR UPDATE", to_id)
        if not a:
            return {"ok": False, "error": "no_user"}
        if not b:
            return {"ok": False, "error": "no_target"}
        if b["blocked"]:
            return {"ok": False, "error": "target_blocked"}
        today = now_local().date()
        sa = dict(a)
        if a["snowballs_day"] != today:
            sa["snowballs_left"] = Config.SNOWBALLS_PER_DAY
        if sa["snowballs_left"] <= 0:
            return {"ok": False, "error": "no_snowballs"}
        await conn.execute(
            """UPDATE users SET snowballs_left = $2 - 1, snowballs_day = $3,
                   snowballs_sent = snowballs_sent + 1
               WHERE tg_id = $1""",
            from_id, sa["snowballs_left"], today)
        await conn.execute(
            "UPDATE users SET snowballs_recv = snowballs_recv + 1 WHERE tg_id = $1",
            to_id)
        return {"ok": True,
                "target_login": b["username"] or f"id{to_id}",
                "target_display": b["display_name"] or b["username"] or f"id{to_id}"}


# ---------------------------------------------------------------- достижения

ACH_DEFS = {
    # code: (заголовок, предикат от row users)
    "take_part": ("🎄 «Я в деле!» — участник ивента", lambda u: True),
    "first_decor": ("🌱 «Три иголки» — первый шар", lambda u: u["decor_total"] >= 1),
    "decor_10": ("✨ 10 украшений", lambda u: u["decor_total"] >= 10),
    "decor_50": ("✨ 50 украшений", lambda u: u["decor_total"] >= 50),
    "streak_3": ("🔥 Серия 3 дня", lambda u: u["best_streak"] >= 3),
    "streak_7": ("🔥 Серия 7 дней", lambda u: u["best_streak"] >= 7),
    "streak_14": ("🔥 Серия 14 дней", lambda u: u["best_streak"] >= 14),
    "streak_21": ("🔥 Серия 21 день", lambda u: u["best_streak"] >= 21),
    "streak_28": ("🏆 Серия 28 дней", lambda u: u["best_streak"] >= 28),
    "first_snow": ("❄️ Первый снежок", lambda u: u["snowballs_sent"] >= 1),
    "snow_25": ("❄️ 25 отправленных снежков", lambda u: u["snowballs_sent"] >= 25),
    "recv_25": ("☃️ 25 полученных снежков", lambda u: u["snowballs_recv"] >= 25),
    "b50": ("📈 «Всё идёт по плану» — 50 шаров", lambda u: u["ornaments"] >= 50),
    "b100": ("💫 «Почти как у бабушки» — 100 шаров", lambda u: u["ornaments"] >= 100),
    "b150": ("⭐ «Звезда района» — 150 шаров", lambda u: u["ornaments"] >= 150),
    "b250": ("🏙 «Городская ёлка» — 250 шаров", lambda u: u["ornaments"] >= 250),
    "b400": ("📰 «Местные в шоке» — 400 шаров", lambda u: u["ornaments"] >= 400),
    "b500": ("🗿 «Лесной босс» — 500 шаров", lambda u: u["ornaments"] >= 500),
    "b600": ("🛰 «Видно из космоса» — 600 шаров", lambda u: u["ornaments"] >= 600),
    "b700": ("⚠️ «Хвойная аномалия» — 700 шаров", lambda u: u["ornaments"] >= 700),
    "b825": ("☢️ «Хвойная угроза» — 825 шаров", lambda u: u["ornaments"] >= 825),
    "b1000": ("🌌 «Ёлка галактического масштаба» — 1000 шаров", lambda u: u["ornaments"] >= 1000),
}
# коды ачивок с прозрачным порогом, для которых нельзя «нагнать» lp
DISCRETION = {"take_part": "registration"}


async def check_achievements(uid: int, on_award=None) -> list[str]:
    """Выдаёт достижения, возвращает список названий НОВых. on_award(uid, code, title) — опционально."""
    async with pool().acquire() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", uid)
        if not u:
            return []
        have = {r["code"] for r in await conn.fetch(
            "SELECT code FROM achievements WHERE tg_id = $1", uid)}
        new = []
        for code, (title, pred) in ACH_DEFS.items():
            if code not in have and pred(u):
                await conn.execute(
                    "INSERT INTO achievements (tg_id, code) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    uid, code)
                if on_award:
                    await on_award(uid, code, title)
                new.append(title)
        return new


async def user_achievements(uid: int) -> list[str]:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT code FROM achievements WHERE tg_id = $1 ORDER BY created_at", uid)
        return [ACH_DEFS[r["code"]][0] for r in rows if r["code"] in ACH_DEFS]


# ---------------------------------------------------------------- рейтинги

RATING_KINDS = {
    "ornaments": "SELECT tg_id, display_name, username, ornaments AS value FROM users WHERE blocked = FALSE ORDER BY ornaments DESC, registered_at LIMIT $1",
    "snipers": "SELECT tg_id, display_name, username, snowballs_sent AS value FROM users WHERE blocked = FALSE ORDER BY snowballs_sent DESC, registered_at LIMIT $1",
    "snowmen": "SELECT tg_id, display_name, username, snowballs_recv AS value FROM users WHERE blocked = FALSE ORDER BY snowballs_recv DESC, registered_at LIMIT $1",
    "decorators": "SELECT tg_id, display_name, username, best_streak AS value FROM users WHERE blocked = FALSE ORDER BY best_streak DESC, registered_at LIMIT $1",
}


async def leaderboard(kind: str, limit: int = 5) -> list:
    async with pool().acquire() as conn:
        return await conn.fetch(RATING_KINDS[kind], limit)


async def rank_of(uid: int, kind: str) -> tuple[int, int, int]:
    """(место, всего участников, значение лидера) — для анонимной обратной связи."""
    col = {"ornaments": "ornaments", "snipers": "snowballs_sent",
           "snowmen": "snowballs_recv", "decorators": "best_streak"}[kind]
    async with pool().acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM users WHERE blocked = FALSE")
        me = await conn.fetchval(f"SELECT {col} FROM users WHERE tg_id = $1 AND blocked = FALSE", uid)
        if me is None:
            return 0, total, 0
        rank = await conn.fetchval(
            f"""SELECT COUNT(*) + 1 FROM users
                WHERE blocked = FALSE AND ({col} > $1
                   OR ({col} = $1 AND registered_at <
                       (SELECT registered_at FROM users WHERE tg_id = $2)))""",
            me, uid)
        lead = await conn.fetchval(
            f"SELECT {col} FROM users WHERE blocked = FALSE ORDER BY {col} DESC LIMIT 1")
        return rank, total or 0, lead or 0
