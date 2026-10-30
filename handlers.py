"""Хендлеры TG-бота: меню, inline-кнопки, регистрация, анонимный рейтинг, админ-команды."""

import datetime
import os
import random
import secrets

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from config import Config, now_local
from database import (get_user, is_whitelisted, whitelist_display_name, log_event, pool,
                     registration_state, register_user)
from game import (fmt_delta, next_decor_at, snowball_left, throw_snowball,
                  user_achievements, decorate, check_achievements,
                  TIER_IMAGES, current_tree_tier)
from views import tree_progress_line
from views import (admin_participants_text, admin_rating_text, admin_stats_text,
                   menu_text, my_ranking_text, name_of, stats_text, esc)

router = Router()

TG_COMMANDS = {"decor": "/decor", "snow": "/snow имя", "rank": "/top", "me": "/me"}

# ---------------------------------------------------------------- финал ивента

FINAL_WINNERS_PER_KIND = 3  # сколько мест в каждой номинации объявляем победителями

# Пароль на /delete_db — через переменную окружения DB_RESET_PASSWORD (не в коде!)
DB_RESET_PASSWORD = os.getenv("DB_RESET_PASSWORD", "")


async def _final_text(bot, uid: int) -> str:
    """Итоги ивента: если uid среди победителей — поздравление, иначе — благодарность."""
    from game import leaderboard
    wins = []
    for title, kind in [
        ("🎄 Самая нарядная ёлка", "ornaments"),
        ("🎯 Главный снайпер", "snipers"),
        ("🌟 Декоратор (лучшая серия)", "decorators"),
        ("☃️ Снеговик", "snowmen"),
    ]:
        rows = await leaderboard(kind, FINAL_WINNERS_PER_KIND)
        for i, r in enumerate(rows, 1):
            if r["tg_id"] == uid:
                wins.append((title, i, r["value"]))
    if wins:
        lines = ["🏆 ПОЗДРАВЛЯЕМ! Ты в числе победителей новогоднего ивента!\n"]
        for title, place, value in wins:
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}[place]
            lines.append(f"{medal} {title} — {place} место")
        lines.append("\nСпасибо, что участвовал — это было здорово! 🎄")
        return "\n".join(lines)
    return ("🎄 Новогодний ивент завершён!\n\n"
            "Спасибо за участие — ёлки, снежки, серии. "
            "Результаты объявим, а пока — отдыхай и готовься к следующему ивенту! 🎅")


@router.message(Command("admin_final_broadcast"))
async def cmd_admin_final_broadcast(message: Message):
    """Рассылка итогов: победителям — поздравление, остальным — спасибо за участие."""
    if not is_admin(message.from_user.id):
        return
    from database import pool
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT tg_id, display_name, username FROM users WHERE blocked = FALSE")
    if not rows:
        await message.answer("Участников нет — рассылать нечего.")
        return
    ok, fail = 0, 0
    for r in rows:
        try:
            text = await _final_text(message.bot, r["tg_id"])
            await message.bot.send_message(r["tg_id"], text)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(
        f"📤 Рассылка итогов завершена: {ok} получено, {fail} ошибок "
        f"(участник не открыл бота).")


# ---------------------------------------------------------------- /delete_db

# Состояние: uid -> (stage, expected, expires)
# stage: "password" | "captcha"; expected: пароль или ответ капчи
_reset_state: dict[int, tuple[str, str, datetime.datetime]] = {}


@router.message(Command("delete_db"))
async def cmd_delete_db(message: Message):
    """Сброс БД: пароль + капча + бэкап в чат. Только админ."""
    if not is_admin(message.from_user.id):
        return
    if not DB_RESET_PASSWORD:
        await message.answer("⛔ DB_RESET_PASSWORD не задан в переменных окружения.")
        return
    if message.from_user.id in _reset_state:
        await message.answer("⏳ Уже идёт подтверждение. Отмена: /cancel")
        return
    _reset_state[message.from_user.id] = ("password", "", datetime.datetime.now() + datetime.timedelta(minutes=2))
    await message.answer(
        "🗑 СБРОС БД — ДАННЫЕ БУДУТ УДАЛЕНЫ!\n\n"
        "Шаг 1/2: пришли пароль (он в переменных окружения Амверы).\n"
        "Отмена: /cancel")


@router.message(Command("cancel"))
async def cmd_reset_cancel(message: Message):
    uid = message.from_user.id
    if uid in _reset_state:
        del _reset_state[uid]
        await message.answer("❌ Сброс отменён.")


# Ловим текстовый ответ на пароль/капчу — только если админ в состоянии сброса
@router.message(F.text & ~F.text.startswith("/"))
async def cmd_reset_answer(message: Message):
    uid = message.from_user.id
    if uid not in _reset_state or not is_admin(uid):
        return  # не наш случай — пропускаем дальше
    stage, expected, expires = _reset_state[uid]
    if datetime.datetime.now() > expires:
        del _reset_state[uid]
        await message.answer("⏰ Подтверждение истекло (2 мин). Начните заново: /delete_db")
        return
    text = (message.text or "").strip()
    if stage == "password":
        if not secrets.compare_digest(text, DB_RESET_PASSWORD):
            del _reset_state[uid]
            await message.answer("❌ Неверный пароль. Попробуйте снова: /delete_db")
            return
        a, b = random.randint(10, 99), random.randint(10, 99)
        _reset_state[uid] = ("captcha", str(a + b), datetime.datetime.now() + datetime.timedelta(minutes=2))
        await message.answer(f"Шаг 2/2: капча. Сколько будет {a} + {b}?\nОтмена: /cancel")
        return
    # captcha
    if text != expected:
        del _reset_state[uid]
        await message.answer("❌ Неверный ответ капчи. Попробуйте снова: /delete_db")
        return
    del _reset_state[uid]
    await _do_reset_db(message)


async def _do_reset_db(message: Message):
    """Бэкап в чат + очистка таблиц (в транзакции). Whitelist сохраняется."""
    from database import pool, log_event
    async with pool().acquire() as conn:
        users = await conn.fetch(
            """SELECT tg_id, username, display_name, registered_at, ornaments,
                      best_streak, snowballs_sent, snowballs_recv, blocked
               FROM users""")
        ach = await conn.fetch("SELECT tg_id, code FROM achievements")
        wl = await conn.fetch("SELECT username, display_name FROM whitelist")
    lines = ["📦 БЭКАП ПЕРЕД СБРОСОМ БД",
             f"Время: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"Пользователей: {len(users)} | Достижений: {len(ach)}"]
    if wl:
        lines.append("\n-- whitelist (не сбрасывается) --")
        for r in wl:
            lines.append(f"{r['username']}|{r['display_name'] or ''}")
    if users:
        lines.append("\n-- users --")
        for r in users:
            lines.append(
                f"{r['tg_id']}|{r['username'] or ''}|{r['display_name'] or ''}|"
                f"{r['registered_at'].strftime('%Y-%m-%d')}|"
                f"ornaments={r['ornaments']} best_streak={r['best_streak']}|"
                f"snow={r['snowballs_sent']}/{r['snowballs_recv']}|"
                f"blocked={int(r['blocked'])}")
    if ach:
        lines.append("\n-- achievements (tg_id:code) --")
        for r in ach:
            lines.append(f"{r['tg_id']}:{r['code']}")
    blob = "\n".join(lines)
    for i in range(0, len(blob), 4000):
        await message.answer(f"```{blob[i:i+4000]}```")
    async with pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM event_log")
            await conn.execute("DELETE FROM achievements")
            await conn.execute("DELETE FROM users")
    await log_event(message.from_user.id, "", "delete_db", "full reset (backup in chat)")
    await message.answer(
        "✅ БД сброшена. Бэкап выше — сохрани его, если захочешь восстановить.\n"
        "Whitelist не трогала (это список участников, а не прогресс).")


# ---------------------------------------------------------------- старт / регистрация

@router.message(CommandStart())
async def cmd_start(message: Message):
    if message.chat.type != "private":
        return
    now = now_local()
    state = registration_state(now.date())
    uid = message.from_user.id
    u = await get_user(uid)

    if u and not u["blocked"]:
        # Фото с подписью (звание + прогресс)
        await send_tree_image(message.bot, uid, u["ornaments"])
        # Меню с кнопками
        await show_menu(message, edit=False)
        return

    uname = (message.from_user.username or "").lower()
    wl_name = await whitelist_display_name(uname)
    allowed = bool(wl_name) or uid in Config.ADMIN_IDS

    if not allowed:
        await message.answer(
            "😔 К сожалению, вы не можете принять участие в этом ивенте.\n"
            "Если это ошибка — напишите организатору.")
        await log_event(uid, uname or "?", "start_denied")
        return

    if state == "before":
        await message.answer(
            "🎄 Привет! Это бот новогоднего ивента.\n"
            f"Регистрация откроется {Config.REGISTRATION_START.strftime('%d.%m.%Y')}.")
        return
    if state == "closed" and uid not in Config.ADMIN_IDS:
        await message.answer("🔒 Регистрация на ивент закрыта.")
        await log_event(uid, uname or "?", "start_registration_closed")
        return
    if state == "closed":
        await message.answer("🔒 Регистрация закрыта. Админ: /admin_register telegram_id Имя.")
        return

    await register_user(uid, uname, wl_name or message.from_user.first_name)
    await log_event(uid, uname, "register")
    await check_achievements(uid)  # 🎄 «Я в деле!» — за само участие
    await message.answer(
        "🎄 Добро пожаловать в новогодний ивент!\n"
        "✅ Регистрация прошла успешно.\n"
        "🏅 Достижение получено: 🎄 «Я в деле!»\n"
        + (f"С {Config.EVENT_START.strftime('%d.%m')} начинается игра."
           if now.date() < Config.EVENT_START else "Игра уже идёт — вперёд!"))
    # Фото с подписью
    await send_tree_image(message.bot, uid, 0)
    # Меню с кнопками
    await show_menu(message, edit=False)


# ---------------------------------------------------------------- украшение

@router.callback_query(F.data == "decor")
async def cb_decor(cb: CallbackQuery):
    uid = cb.from_user.id
    before = await get_user(uid)
    if not before or before["blocked"]:
        return
    today = now_local().date()
    if today < Config.EVENT_START:
        await cb.answer("⏳ Игра ещё не началась!", show_alert=True)
        return
    if today > Config.EVENT_END:
        await cb.answer("🏁 Ивент завершён.", show_alert=True)
        return
    res = await decorate(uid)
    if not res["ok"]:
        if res["error"] == "cooldown":
            await cb.answer(f"✨ Следующий шар через {fmt_delta(res['wait'])}", show_alert=True)
        else:
            await cb.answer("⛔ Недоступно", show_alert=True)
        return

    lines = [f"✨ На ёлке стало на {res['balls']} шар(а/ов) больше."
             + (f" (бонус серии: +{res['bonus']})" if res["bonus"] else "")]
    if not res["in_day_window"]:
        lines.append("🌙 Ночное украшение: шары вешаются, но серия не растёт.")
    lines.append(f"🎄 Всего шаров: {res['ornaments']}")
    lines.append(f"🔥 Серия: {res['streak']} дн. | 🏆 Лучшая: {res['best']} дн.")

    new_ach = await check_achievements(uid)
    if new_ach:
        lines.append("\n🏅 Достижения: " + ", ".join(new_ach))
    await log_event(uid, before["username"] or "", "decor",
                    f"+{res['balls']} -> {res['ornaments']}")
    await cb.answer()
    # результат — отдельным сообщением вниз, меню при этом обновляется (edit)
    await cb.message.answer("\n".join(lines))
    await show_menu(cb, edit=True)
    
    # Если пересёк порог звания — отправляем новое фото автоматически
    old_tier = current_tree_tier(before["ornaments"])
    new_tier = current_tree_tier(res["ornaments"])
    if old_tier != new_tier:
        await send_tree_image(cb.bot, uid, res["ornaments"])


# ---------------------------------------------------------------- снежки

@router.callback_query(F.data == "snow_start")
async def cb_snow_start(cb: CallbackQuery):
    uid = cb.from_user.id
    u = await get_user(uid)
    if not u or u["blocked"]:
        return
    left = await snowball_left(uid)
    if left <= 0:
        await cb.answer("❄️ Сегодня снежков больше нет. Новые появятся завтра.", show_alert=True)
        return
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT tg_id, display_name, username FROM users
               WHERE tg_id != $1 AND blocked = FALSE
               ORDER BY display_name NULLS LAST LIMIT 40""", uid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=esc((r["display_name"] or r["username"]) or f"id{r['tg_id']}")[:40],
            callback_data=f"snow:{r['tg_id']}")]
        for r in rows])
    await cb.message.answer(f"❄️ В кого кидаем снежок? (у вас их {left})", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("snow:"))
async def cb_snow_throw(cb: CallbackQuery):
    uid = cb.from_user.id
    target_id = int(cb.data.split(":")[1])
    res = await throw_snowball(uid, target_id)
    if not res["ok"]:
        msg = {"no_snowballs": "❄️ Снежки закончились до завтра.",
               "no_target": "⛔ Участник не найден.",
               "target_blocked": "⛔ Участник заблокирован."}.get(res["error"], "⛔ Ошибка")
        await cb.answer(msg, show_alert=True)
        return
    await cb.answer()
    me = await get_user(uid)
    await cb.message.edit_text(f"❄️ Вы закидали снежком: {esc(res['target_display'])}!")
    await log_event(uid, me["username"] or "", "snowball_sent", f"to id{target_id}")
    new_ach = await check_achievements(uid)
    if new_ach:
        await cb.message.answer("🏅 Достижения: " + ", ".join(new_ach))
    try:
        await cb.bot.send_message(
            target_id, f"❄️ В вас прилетел снежок от {name_of(me)}!")
    except Exception:
        pass


# ---------------------------------------------------------------- рейтинг (анонимный) и статистика

@router.callback_query(F.data == "ranking")
async def cb_ranking(cb: CallbackQuery):
    u = await get_user(cb.from_user.id)
    if not u:
        return
    text = await my_ranking_text(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")]])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "stats")
async def cb_stats(cb: CallbackQuery):
    u = await get_user(cb.from_user.id)
    if not u:
        return
    text = await stats_text(u)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")]])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "menu")
async def cb_menu(cb: CallbackQuery):
    await show_menu(cb, edit=True)
    await cb.answer()


# текстовые дубли кнопок (удобно с телефона из любого чата)
@router.message(Command("decor"))
async def cmd_decor(message: Message):
    uid = message.from_user.id
    u = await get_user(uid)
    if not u or u["blocked"]:
        return
    today = now_local().date()
    if today < Config.EVENT_START:
        await message.answer("⏳ Игра ещё не началась!")
        return
    if today > Config.EVENT_END:
        await message.answer("🏁 Ивент завершён.")
        return
    res = await decorate(uid)
    if not res["ok"]:
        if res["error"] == "cooldown":
            await message.answer(f"✨ Следующий шар через {fmt_delta(res['wait'])}")
        else:
            await message.answer("⛔ Недоступно")
        return
    lines = [f"✨ +{res['balls']} шар(а/ов)." + (f" Бонус серии: +{res['bonus']}." if res["bonus"] else "")]
    if not res["in_day_window"]:
        lines.append("🌙 Ночное украшение: серия не растёт.")
    lines.append(f"🎄 Всего шаров: {res['ornaments']}")
    lines.append(f"🔥 Серия: {res['streak']} дн. | 🏆 Лучшая: {res['best']} дн.")
    new_ach = await check_achievements(uid)
    if new_ach:
        lines.append("🏅 Достижения: " + ", ".join(new_ach))
    await log_event(uid, u["username"] or "", "decor", f"+{res['balls']} -> {res['ornaments']}")
    await message.answer("\n".join(lines))

@router.message(Command("snow"))
async def cmd_snow(message: Message, command: CommandObject):
    uid = message.from_user.id
    u = await get_user(uid)
    if not u or u["blocked"]:
        return
    if not command.args:
        async with pool().acquire() as conn:
            rows = await conn.fetch(
                "SELECT display_name, username FROM users WHERE tg_id != $1 AND blocked = FALSE ORDER BY display_name NULLS LAST LIMIT 40", uid)
        names = [esc(r["display_name"] or r["username"] or "?") for r in rows]
        await message.answer("❄️ Кинуть снежок: /snow имя-участника\nУчастники: " + ", ".join(names))
        return
    target_name = command.args.strip().lower()
    async with pool().acquire() as conn:
        t = await conn.fetchrow(
            """SELECT tg_id FROM users
               WHERE blocked = FALSE AND tg_id != $1
                 AND (LOWER(display_name) = $2 OR LOWER(username) = $2)""",
            uid, target_name)
    if not t:
        await message.answer("⛔ Участник не найден. /snow без аргументов — список.")
        return
    res = await throw_snowball(uid, t["tg_id"])
    if not res["ok"]:
        await message.answer({"no_snowballs": "❄️ Снежки закончились до завтра.",
                              "no_target": "⛔ Участник не найден."}.get(res["error"], "⛔ Ошибка"))
        return
    me = await get_user(uid)
    await message.answer("❄️ Вы закидали снежком: " + esc(res["target_display"]) + "!")
    await log_event(uid, me["username"] or "", "snowball_sent", f"to id{t['tg_id']}")
    new_ach = await check_achievements(uid)
    if new_ach:
        await message.answer("🏅 Достижения: " + ", ".join(new_ach))
    try:
        await message.bot.send_message(t["tg_id"], f"❄️ В вас прилетел снежок от {name_of(me)}!")
    except Exception:
        pass


@router.message(Command("top"))
async def cmd_top(message: Message):
    u = await get_user(message.from_user.id)
    if not u:
        return
    await message.answer(await my_ranking_text(message.from_user.id))


@router.message(Command("me"))
async def cmd_me(message: Message):
    u = await get_user(message.from_user.id)
    if not u:
        return
    await message.answer(await stats_text(u))


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await show_menu(message, edit=False)


# ---------------------------------------------------------------- админ

def is_admin(uid: int) -> bool:
    return uid in Config.ADMIN_IDS


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        f"🛠 Админ-панель\n\n"
        f"/admin_stats — сводка + последние события\n"
        f"/admin_list — все участники с полными данными\n"
        f"/admin_rating — топ по номинациям\n"
        f"/admin_add username — добавить в whitelist\n"
        f"/admin_remove username — убрать из whitelist\n"
        f"/admin_orbs username N — начислить/убрать шары (N — число, можно отрицательное)\n"
        f"/admin_register telegram_id Имя — принудительная регистрация\n"
        f"/admin_block telegram_id — заблокировать\n"
        f"/admin_unblock telegram_id — разблокировать\n"
        f"/admin_log N — последние N событий\n"
        f"/admin_final — итоги ивента (только тебе)\n"
        f"/admin_final_broadcast — рассылка итогов всем (победителям — поздравление, остальным — спасибо)\n"
        f"/delete_db — сброс БД (пароль + капча + бэкап в чат)")


@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(await admin_stats_text())


@router.message(Command("admin_list"))
async def cmd_admin_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(await admin_participants_text())


@router.message(Command("admin_rating"))
async def cmd_admin_rating(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(await admin_rating_text())


@router.message(Command("admin_add"))
async def cmd_admin_add(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /admin_add username|Имя Фамилия "
                             "(или просто username)")
        return
    args = command.args.strip()
    fio = None
    if "|" in args:
        args, fio = args.split("|", 1)
        fio = fio.strip()
    uname = args.strip().lstrip("@").lower()
    async with pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO whitelist (username, display_name) VALUES ($1, $2)
               ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name""",
            uname, fio)
    await log_event(message.from_user.id, "", "admin_add", uname)
    await message.answer(f"✅ @{uname} добавлен в whitelist.")


@router.message(Command("admin_remove"))
async def cmd_admin_remove(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /admin_remove username")
        return
    uname = command.args.strip().lstrip("@").lower()
    async with pool().acquire() as conn:
        await conn.execute("DELETE FROM whitelist WHERE username = $1", uname)
    await log_event(message.from_user.id, "", "admin_remove", uname)
    await message.answer(f"🗑 @{uname} убран из whitelist.")


@router.message(Command("admin_register"))
async def cmd_admin_register(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    parts = (command.args or "").split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        await message.answer("Использование: /admin_register telegram_id Имя")
        return
    tg_id = int(parts[0])
    disp = parts[1].strip() if len(parts) > 1 else "Участник"
    async with pool().acquire() as conn:
        await conn.execute(
            """INSERT INTO users (tg_id, display_name, snowballs_left, snowballs_day)
               VALUES ($1, $2, $3, $4) ON CONFLICT (tg_id) DO NOTHING""",
            tg_id, disp, Config.SNOWBALLS_PER_DAY, now_local().date())
    await log_event(message.from_user.id, "", "admin_register", f"id{tg_id} {disp}")
    await message.answer(f"✅ id{tg_id} ({disp}) зарегистрирован(а).")


@router.message(Command("admin_block"))
async def cmd_admin_block(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /admin_block telegram_id")
        return
    async with pool().acquire() as conn:
        await conn.execute("UPDATE users SET blocked = TRUE WHERE tg_id = $1",
                           int(command.args.strip()))
    await message.answer("⛔ Участник заблокирован.")


@router.message(Command("admin_unblock"))
async def cmd_admin_unblock(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Использование: /admin_unblock telegram_id")
        return
    async with pool().acquire() as conn:
        await conn.execute("UPDATE users SET blocked = FALSE WHERE tg_id = $1",
                           int(command.args.strip()))
    await message.answer("✅ Участник разблокирован.")


@router.message(Command("admin_orbs"))
async def cmd_admin_orbs(message: Message, command: CommandObject):
    """Начислить/убрать шары: /admin_orbs username N (N может быть отрицательным)."""
    if not is_admin(message.from_user.id):
        return
    if not command.args:
        await message.answer("Использование: /admin_orbs username N (N — число шаров, можно отрицательное)")
        return
    parts = command.args.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /admin_orbs username N (N — число шаров, можно отрицательное)")
        return
    uname = parts[0].strip().lstrip("@").lower()
    delta = int(parts[1])
    async with pool().acquire() as conn:
        u = await conn.fetchrow(
            "SELECT tg_id, username, ornaments FROM users WHERE LOWER(username) = $1", uname)
        if not u:
            await message.answer(f"⛔ Участник @{uname} не найден.")
            return
        new_orbs = u["ornaments"] + delta
        if new_orbs < 0:
            await message.answer(f"⛔ Нельзя убрать больше шаров, чем у участника (сейчас {u['ornaments']}).")
            return
        await conn.execute(
            "UPDATE users SET ornaments = $2 WHERE tg_id = $1", u["tg_id"], new_orbs)
        await log_event(message.from_user.id, uname, "admin_orbs", f"{delta:+d} -> {new_orbs}")
    await message.answer(f"✅ @{uname}: {u['ornaments']} {delta:+d} → {new_orbs} шаров.")


@router.message(Command("admin_log"))
async def cmd_admin_log(message: Message, command: CommandObject):
    if not is_admin(message.from_user.id):
        return
    n = 20
    if command.args and command.args.strip().isdigit():
        n = min(int(command.args.strip()), 100)
    async with pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT ts, username, action, details FROM event_log ORDER BY ts DESC LIMIT $1", n)
    if not rows:
        await message.answer("Лог пуст.")
        return
    await message.answer("📜 Лог:\n" + "\n".join(
        f"· {r['ts'].strftime('%d.%m %H:%M')} {r['username'] or '—'}: {r['action']} {r['details'] or ''}"
        for r in rows))


@router.message(Command("admin_final"))
async def cmd_admin_final(message: Message):
    if not is_admin(message.from_user.id):
        return
    from game import leaderboard
    text = "🏁 ИТОГИ ИВЕНТА (полный reveal)\n"
    for title, kind, fmt in [
        ("🎄 Самая нарядная ёлка", "ornaments", "{} шаров"),
        ("🎯 Главный снайпер", "snipers", "{} ❄️"),
        ("🌟 Декоратор (серия)", "decorators", "серия {}"),
        ("☃️ Снеговик", "snowmen", "{} ❄️"),
    ]:
        text += f"\n{title}\n"
        for i, r in enumerate(await leaderboard(kind, 3), 1):
            text += f"{i}. {r['display_name'] or r['username']} (@{r['username'] or '—'}): {fmt.format(r['value'])}\n"
    await message.answer(text)
