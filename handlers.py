"""Хендлеры TG-бота: меню, inline-кнопки, регистрация, анонимный рейтинг, админ-команды."""

import datetime

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


# ---------------------------------------------------------------- helpers

def menu_kb(u, can_decor: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text="✨ Повесить шар" if can_decor else "✨ Пока нельзя — кулдаун",
            callback_data="decor")],
        [InlineKeyboardButton(text="❄️ Кинуть снежок", callback_data="snow_start")],
        [InlineKeyboardButton(text="🏆 Моё место", callback_data="ranking"),
         InlineKeyboardButton(text="👤 Статистика", callback_data="stats")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_menu(cb_or_msg, edit: bool):
    message = cb_or_msg.message if isinstance(cb_or_msg, CallbackQuery) else cb_or_msg
    uid = cb_or_msg.from_user.id
    u = await get_user(uid)
    if not u or u["blocked"]:
        return
    can = True
    if u["last_decor"]:
        can = now_local() >= next_decor_at(u["last_decor"])
    text = await menu_text(u, TG_COMMANDS)
    kb = menu_kb(u, can)
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
        except Exception:
            pass
    else:
        await message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------- картинка звания

async def send_tree_image(bot, uid: int, ornaments: int):
    """Фото текущего звания ёлки — отдельным сообщением в личку."""
    import logging
    import os
    # путь от корня проекта (где лежит handlers.py) — не зависит от cwd процесса
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        TIER_IMAGES[current_tree_tier(ornaments)])
    if not os.path.exists(path):
        logging.warning("tree image not found: %s", path)
        return
    try:
        with open(path, "rb") as f:
            await bot.send_photo(uid, BufferedInputFile(f, filename=os.path.basename(path)),
                                 caption=tree_progress_line(ornaments))
    except Exception:
        logging.exception("failed to send tree image")
        # фото — приятность, не роняем основной сценарий


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
    await show_menu(message, edit=False)
    await send_tree_image(message.bot, uid, 0)


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
        f"/admin_register telegram_id Имя — принудительная регистрация\n"
        f"/admin_block telegram_id — заблокировать\n"
        f"/admin_unblock telegram_id — разблокировать\n"
        f"/admin_log N — последние N событий\n"
        f"/admin_final — итоги ивента (полный reveal)")


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
