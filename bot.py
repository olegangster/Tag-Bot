import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from handlers import router

# keep_alive — опционально (нужен только на Render.com)
try:
    from keep_alive import start_keep_alive_server
    _has_keep_alive = True
except ImportError:
    _has_keep_alive = False
    logger.warning("⚠️ keep_alive.py не найден — /health endpoint отключён (OK для локального запуска)")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Локально можно использовать .env.txt, на Render лучше ENV variables
load_dotenv()
load_dotenv(".env.txt")


async def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("❌ BOT_TOKEN не найден (ни в ENV, ни в .env.txt)")
        return

    bot = Bot(token=bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("🚀 Бот запущен и готов к работе!")
    logger.info("✅ Health endpoint: /health (Render)")

    try:
        # на всякий случай удаляем вебхук
        await bot.delete_webhook(drop_pending_updates=True)

        # /health сервер для Render + UptimeRobot (если доступен)
        if _has_keep_alive:
            asyncio.create_task(start_keep_alive_server())

        # polling (ВАЖНО: должен быть только 1 инстанс бота!)
        await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал остановки")

    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
