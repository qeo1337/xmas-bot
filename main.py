"""TG-версия бота. Запуск: python main.py"""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import Config
from database import init_db, load_whitelist
from handlers import router

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("xmas-bot")


async def main():
    if not Config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set — fill .env (see .env.example)")

    await init_db()
    added = await load_whitelist()
    log.info("Whitelist loaded from file: %d entries", len(added))

    bot = Bot(token=Config.BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    log.info("TG bot starting. Event: %s — %s (%s)",
             Config.EVENT_START, Config.EVENT_END, Config.TZ)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
