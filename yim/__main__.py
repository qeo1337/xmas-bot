"""Бот новогоднего ивента для Яндекс Мессенджера.

Отличия от TG-версии:
- идентификация по staff login (whitelist = список логинов);
- нет inline-кнопок — командный интерфейс;
- polling через messages/getUpdates.

Запуск: python -m yim (токен в MESSENGER_ROBOT_TOKEN в .env или окружении).
"""

import asyncio
import logging
import os
import sys

# импорты из корня проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from pathlib import Path
from config import Config, now_local

# В версии для Мессенджера whitelist — это staff-логины из отдельного файла
Config.WHITELIST_FILE = Path(os.getenv("WHITELIST_FILE_YIM", "whitelist_yim.txt"))

from database import (get_user, init_db, is_whitelisted, load_whitelist,
                      log_event, pool, registration_state, register_user)
from game import decorate, check_achievements, throw_snowball, fmt_delta
from views import (admin_participants_text, admin_rating_text, admin_stats_text,
                   menu_text, my_ranking_text, stats_text)
from yim.yim_client import YimClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("xmas-yim")

YIM_COMMANDS = {"decor": "/decor", "snow": "/snow <логин>", "rank": "/top", "me": "/me"}
ADMIN_LOGINS = {x.strip().lower() for x in os.getenv("ADMIN_LOGINS", "").split(",") if x.strip()}


class YimBot:
    def __init__(self):
        token = os.getenv("MESSENGER_ROBOT_TOKEN")
        if not token:
            raise SystemExit("MESSENGER_ROBOT_TOKEN не задан — получите OAuthTeam-токен робота")
        self.client = YimClient(token)
        self.offset = None
        self._known_ids = {}  # login -> числовой id (для tg_id в БД)

    async def reply(self, login: str, text: str):
        """Отправка в личку; ошибки логируем, не роняем цикл."""
        try:
            self.client.send_text_login(login, text)
        except Exception as e:
            log.error("send to %s failed: %s", login, e)

    # ------------------------------------------------------ регистрация и меню

    async def handle_start(self, login: str, uid: int, display_name: str):
        now = now_local()
        state = registration_state(now.date())
        u = await get_user(uid)
        if u and not u["blocked"]:
            await self.show_menu(login, uid)
            return
        if not await is_whitelisted(login):
            await log_event(uid, login, "start_denied")
            await self.reply(login, "😔 К сожалению, вы не можете принять участие в этом ивенте.")
            return
        if state == "before":
            await self.reply(login, "🎄 Привет! Регистрация откроется "
                              f"{Config.REGISTRATION_START.strftime('%d.%m.%Y')}.")
            return
        if state == "closed":
            await log_event(uid, login, "start_registration_closed")
            await self.reply(login, "🔒 Регистрация на ивент закрыта.")
            return
        await register_user(uid, login, display_name or login)
        await log_event(uid, login, "register")
        await check_achievements(uid)
        await self.reply(
            login,
            "🎄 Добро пожаловать в новогодний ивент!\n"
            "✅ Регистрация прошла успешно.\n"
            "🏅 Достижение: 🎄 «Я в деле!»\n"
            + (f"С {Config.EVENT_START.strftime('%d.%m')} начинается игра."
               if now.date() < Config.EVENT_START else "Игра уже идёт — вперёд!"))
        await self.show_menu(login, uid)

    async def show_menu(self, login: str, uid: int):
        u = await get_user(uid)
        if not u or u["blocked"]:
            return
        await self.reply(login, await menu_text(u, YIM_COMMANDS) +
                         "\n/start — это меню")

    # ------------------------------------------------------ игровые действия

    async def handle_decor(self, login: str, uid: int):
        u = await get_user(uid)
        if not u or u["blocked"]:
            return
        today = now_local().date()
        if today < Config.EVENT_START:
            await self.reply(login, "⏳ Игра ещё не началась!")
            return
        if today > Config.EVENT_END:
            await self.reply(login, "🏁 Ивент завершён.")
            return
        res = await decorate(uid)
        if not res["ok"]:
            if res["error"] == "cooldown":
                await self.reply(login, f"✨ Следующий шар через {fmt_delta(res['wait'])}")
            else:
                await self.reply(login, "⛔ Недоступно")
            return
        lines = [f"✨ +{res['balls']} шар(а/ов)." +
                 (f" Бонус серии: +{res['bonus']}." if res["bonus"] else "")]
        if not res["in_day_window"]:
            lines.append("🌙 Ночное украшение: серия не растёт.")
        lines.append(f"🎄 Всего шаров: {res['ornaments']}")
        lines.append(f"🔥 Серия: {res['streak']} дн. | 🏆 Лучшая: {res['best']} дн.")
        new_ach = await check_achievements(uid)
        if new_ach:
            lines.append("🏅 Достижения: " + ", ".join(new_ach))
        await log_event(uid, login, "decor", f"+{res['balls']} -> {res['ornaments']}")
        await self.reply(login, "\n".join(lines))

    async def handle_snow(self, login: str, uid: int, args: str | None):
        u = await get_user(uid)
        if not u or u["blocked"]:
            return
        if not args:
            async with pool().acquire() as conn:
                rows = await conn.fetch(
                    """SELECT display_name, username FROM users
                       WHERE tg_id != $1 AND blocked = FALSE
                       ORDER BY username LIMIT 40""", uid)
            names = [r["username"] or r["display_name"] or "?" for r in rows]
            await self.reply(login, "❄️ Кинуть снежок: **/snow <логин>**\nУчастники: " + ", ".join(names))
            return
        target_login = args.strip().lstrip("@").lower()
        async with pool().acquire() as conn:
            t = await conn.fetchrow(
                "SELECT tg_id, display_name, username FROM users WHERE LOWER(username) = $1 AND tg_id != $2 AND blocked = FALSE",
                target_login, uid)
        if not t:
            await self.reply(login, "⛔ Участник не найден. /snow без аргументов — список.")
            return
        res = await throw_snowball(uid, t["tg_id"])
        if not res["ok"]:
            await self.reply(login, {"no_snowballs": "❄️ Снежки закончились до завтра."}
                             .get(res["error"], "⛔ Ошибка"))
            return
        await log_event(uid, login, "snowball_sent", f"to @{res['target_login']}")
        new_ach = await check_achievements(uid)
        text = f"❄️ Вы закидали снежком: **{res['target_display']}**!"
        if new_ach:
            text += "\n🏅 Достижения: " + ", ".join(new_ach)
        await self.reply(login, text)
        try:  # уведомляем цель
            self.client.send_text_login(res["target_login"],
                                        f"❄️ В вашу ёлку прилетел снежок от **{u['display_name'] or login}**!")
        except Exception as e:
            log.warning("notify target %s failed: %s", res["target_login"], e)

    async def handle_top(self, login: str, uid: int):
        u = await get_user(uid)
        if not u:
            return
        await self.reply(login, await my_ranking_text(uid))

    async def handle_me(self, login: str, uid: int):
        u = await get_user(uid)
        if not u:
            return
        await self.reply(login, await stats_text(u))

    # ------------------------------------------------------ админ

    async def handle_admin(self, login: str, text: str, uid: int):
        if login.lower() not in ADMIN_LOGINS:
            return
        parts = text.split(maxsplit=1)
        cmd = parts[0] if parts else ""
        args = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("/admin", "/admin_stats"):
            await self.reply(login, await admin_stats_text())
        elif cmd == "/admin_list":
            await self.reply(login, await admin_participants_text())
        elif cmd == "/admin_rating":
            await self.reply(login, await admin_rating_text())
        elif cmd == "/admin_add" and args:
            async with pool().acquire() as conn:
                await conn.execute(
                    "INSERT INTO whitelist (username) VALUES ($1) ON CONFLICT DO NOTHING",
                    args.lstrip("@").lower())
            await self.reply(login, f"✅ @{args} добавлен в whitelist.")
        elif cmd == "/admin_remove" and args:
            async with pool().acquire() as conn:
                await conn.execute("DELETE FROM whitelist WHERE username = $1",
                                   args.lstrip("@").lower())
            await self.reply(login, f"🗑 @{args} убран из whitelist.")
        elif cmd == "/admin_log":
            n = int(args) if args.isdigit() else 20
            async with pool().acquire() as conn:
                rows = await conn.fetch(
                    "SELECT ts, username, action, details FROM event_log ORDER BY ts DESC LIMIT $1",
                    min(n, 100))
            await self.reply(login, "📜 Лог:\n" + "\n".join(
                f"· {r['ts'].strftime('%d.%m %H:%M')} {r['username'] or '—'}: {r['action']} {r['details'] or ''}"
                for r in rows))
        else:
            await self.reply(login,
                             "🛠 Команды: /admin_stats, /admin_list, /admin_rating, "
                             "/admin_add <логин>, /admin_remove <логин>, /admin_log N")

    # ------------------------------------------------------ цикл polling

    async def dispatch(self, upd: dict):
        text = (upd.get("text") or "").strip()
        frm = upd.get("from", {})
        login = frm.get("login")
        uid = frm.get("id")
        display_name = frm.get("display_name") or login
        chat_type = upd.get("chat", {}).get("type", "")
        if not login or uid is None or not text:
            return
        uid = int(uid)
        self._known_ids[login] = uid
        log.info("[%s] %s: %s", chat_type, login, text)

        low = text.lower()
        if low.startswith("/start"):
            await self.handle_start(login, uid, display_name)
        elif low.startswith("/decor") or low.startswith("/украсить"):
            await self.handle_decor(login, uid)
        elif low.startswith("/snow"):
            await self.handle_snow(login, uid, text[5:].strip() or None)
        elif low.startswith("/top"):
            await self.handle_top(login, uid)
        elif low.startswith("/me") or low.startswith("/stats"):
            await self.handle_me(login, uid)
        elif low.startswith("/admin"):
            await self.handle_admin(login, text, uid)

    async def run(self):
        await init_db()
        wl_size = await load_whitelist()
        log.info("Whitelist loaded: %d", len(wl_size))
        me = self.client.self_get()
        log.info("YIM bot connected: %s", me)
        log.info("Event: %s — %s (%s)", Config.EVENT_START, Config.EVENT_END, Config.TZ)
        while True:
            try:
                updates = self.client.get_updates(limit=50, offset=self.offset)
                for upd in updates:
                    await self.dispatch(upd)
                if updates:
                    # сдвигаем offset по последнему update_id (если поле есть)
                    last = updates[-1]
                    if "update_id" in last:
                        self.offset = last["update_id"] + 1
            except Exception as e:
                log.exception("polling error: %s", e)
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(1)


async def main():
    bot = YimBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
