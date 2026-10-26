"""Общие тексты/представления: меню, статистика, анонимная обратная связь, админ-выгрузки.
Одинаковые для TG-версии и версии для Яндекс Мессенджера.

ВАЖНО: TG-версия отправляет тексты с parse_mode=HTML — никаких «сырых» <...> в
текстах быть не должно, а всё от пользователей (имена) проходит через esc()."""

import html

from config import Config, now_local
from database import pool
from game import (next_decor_at, fmt_delta, snowball_left,
                  user_achievements, leaderboard, rank_of, ACH_DEFS)


def esc(value: str | None) -> str:
    """Экранирование HTML — для любых данных от пользователей (имена и пр.)."""
    return html.escape(str(value or ""), quote=False)


def name_of(u) -> str:
    return esc(u["display_name"] or u["username"] or "участник")


def event_phase(now=None) -> str:
    now = now or now_local()
    if now.date() > Config.EVENT_END:
        return "🏁 Ивент завершён."
    if now.date() < Config.EVENT_START:
        return f"⏳ Игра начнётся {Config.EVENT_START.strftime('%d.%m.%Y')}!"
    return ""


def _decor_line(u, now) -> str:
    if u["last_decor"]:
        nxt = next_decor_at(u["last_decor"])
        wait = int((nxt - now).total_seconds())
        tzname = {"Europe/Moscow": "МСК"}.get(Config.TZ.key, Config.TZ.key)
        return (f"✨ Следующий шар — {nxt.astimezone(Config.TZ).strftime('%H:%M')} ({tzname})"
                if wait > 0 else "✨ Можно вешать шар!")
    return "✨ Ёлка пока пустая — повесь первый шар!"


async def menu_text(u, commands: dict[str, str] | None = None) -> str:
    """commands: подсказки команд платформы, ключи decor/snow/rank/me.
    ВАЖНО: значения идут прямо в текст TG (HTML) — без угловых скобок!"""
    now = now_local()
    sb = await snowball_left(u["tg_id"])
    head = event_phase(now)
    c = commands or {}
    cmd_lines = []
    if "decor" in c:
        cmd_lines.append(f"✨ Повесить шар: {c['decor']}")
    if "snow" in c:
        cmd_lines.append(f"❄️ Кинуть снежок: {c['snow']}")
    if "rank" in c:
        cmd_lines.append(f"🏆 Твоё место в рейтинге: {c['rank']}")
    if "me" in c:
        cmd_lines.append(f"👤 Моя статистика: {c['me']}")
    return (
        (head + "\n" if head else "") +
        f"🎄 {name_of(u)}, на твоей ёлке — {u['ornaments']} шаров\n"
        f"{_decor_line(u, now)}\n"
        f"❄️ Снежков сегодня: {sb} | Отправлено всего: {u['snowballs_sent']}\n"
        f"☃️ Получено снежков: {u['snowballs_recv']}\n"
        f"🔥 Серия: {u['streak']} дн. | 🏆 Лучшая: {u['best_streak']} дн."
        + ("\n\n" + "\n".join(cmd_lines) if cmd_lines else ""))


async def stats_text(u) -> str:
    ach = await user_achievements(u["tg_id"])
    return (
        f"👤 {name_of(u)}\n\n"
        f"🎄 Шаров на ёлке: {u['ornaments']}\n"
        f"✨ Украшений всего: {u['decor_total']}\n"
        f"🔥 Текущая серия: {u['streak']} дн.\n"
        f"🏆 Лучшая серия: {u['best_streak']} дн.\n"
        f"❄️ Отправлено снежков: {u['snowballs_sent']}\n"
        f"☃️ Получено снежков: {u['snowballs_recv']}\n\n"
        f"🏅 Достижения ({len(ach)}/{len(ACH_DEFS)}):\n"
        + "\n".join(
            f"✅ {esc(t)}" if t in ach else "❌ ***"
            for t in [d[0] for d in ACH_DEFS.values()]))


# ------------------------------------------------- анонимная обратная связь
# Рейтинг для пользователей закрыт: видно только своё место, число участников
# и отставание от лидера. Полные списки — только админам.

RATING_TITLES = [
    ("🎄 Самая нарядная ёлка", "ornaments", "шаров"),
    ("🎯 Главные снайперы", "snipers", "снежков"),
    ("🌟 Декоратор (лучшая серия)", "decorators", "дн."),
    ("☃️ Снеговик", "snowmen", "снежков"),
]


async def my_ranking_text(uid: int) -> str:
    text = "🏆 Твоё место в номинациях\n"
    for title, kind, unit in RATING_TITLES:
        rank, total, lead = await rank_of(uid, kind)
        if not total:
            text += f"\n{title}: пока нет участников\n"
            continue
        if rank == 0:
            text += f"\n{title}: ты не участвуешь\n"
            continue
        if rank == 1:
            text += f"\n{title}: {rank} место из {total} — ты лидер 🥇\n"
        else:
            line = f"\n{title}: {rank} место из {total}"
            if lead > 0:
                line += f" (до лидера {lead} {unit})"
            text += line + "\n"
    text += "\nПолный рейтинг скрыт до финала ивента 🎁"
    return text


# ------------------------------------------------- админ-выгрузки (полная прозрачность)

async def admin_rating_text(limit: int = 5) -> str:
    text = f"📊 Админ-рейтинг (топ-{limit}, полные данные)\n"
    for title, kind, fmt in [
        ("🎄 Ёлки (шары)", "ornaments", "{} шаров"),
        ("🎯 Снайперы", "snipers", "{} ❄️"),
        ("🌟 Декоратор (серия)", "decorators", "серия {}"),
        ("☃️ Снеговики", "snowmen", "{} ❄️"),
    ]:
        text += f"\n{title}\n"
        rows = await leaderboard(kind, limit)
        if not rows:
            text += "— пусто —\n"
        for i, r in enumerate(rows, 1):
            text += (f"{i}. {esc(r['display_name'] or '—')} (@{esc(r['username'] or '—')}): "
                     f"{fmt.format(r['value'])}\n")
    return text


async def admin_participants_text() -> str:
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT tg_id, username, display_name, ornaments, best_streak,
                      snowballs_sent, snowballs_recv, blocked, registered_at
               FROM users ORDER BY registered_at""")
        wl = await conn.fetchval("SELECT COUNT(*) FROM whitelist")
    if not rows:
        return f"Whitelist: {wl}. Пока никто не зарегистрировался."
    lines = [f"Whitelist: {wl} | Зарегистрировано: {len(rows)}\n"]
    for r in rows:
        mark = "⛔" if r["blocked"] else "✅"
        lines.append(
            f"{mark} {esc(r['display_name'] or '—')} (@{esc(r['username'] or '—')}, id{r['tg_id']}) — "
            f"{r['ornaments']} шаров, серия {r['best_streak']}, "
            f"❄️{r['snowballs_sent']}/{r['snowballs_recv']}, "
            f"рег. {r['registered_at'].strftime('%d.%m')}")
    return "\n".join(lines)


async def admin_stats_text() -> str:
    async with pool().acquire() as conn:
        wl = await conn.fetchval("SELECT COUNT(*) FROM whitelist")
        reg = await conn.fetchval("SELECT COUNT(*) FROM users WHERE blocked = FALSE")
        active_today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE streak_last_day = CURRENT_DATE")
        decor_total = await conn.fetchval("SELECT COALESCE(SUM(decor_total),0) FROM users")
        snow_total = await conn.fetchval("SELECT COALESCE(SUM(snowballs_sent),0) FROM users")
        last_actions = await conn.fetch(
            "SELECT ts, username, action, details FROM event_log ORDER BY ts DESC LIMIT 15")
    text = (
        f"🛠 Сводка ивента\n"
        f"В whitelist: {wl} | Зарегистрировано: {reg} | Активны сегодня: {active_today}\n"
        f"Всего украшений: {decor_total} | Всего снежков: {snow_total}\n\n"
        f"Последние события (лог):\n")
    for r in last_actions:
        text += (f"· {r['ts'].strftime('%d.%m %H:%M')} {esc(r['username'] or '—')}: "
                 f"{esc(r['action'])} {esc(r['details'] or '')}\n")
    return text
