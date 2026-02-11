import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from keep_alive import start_keep_alive_server
from handlers import router

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Загружаем .env (если он есть локально). На Render просто пропустится.
load_dotenv()

async def main():
    """Главная функция запуска бота"""

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("❌ BOT_TOKEN не найден в переменных окружения (Render Environment Variables)")
        return

    bot = Bot(token=bot_token)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(router)

    logger.info("🚀 Бот запущен и готов к работе!")

    try:
        # Поднимаем HTTP /health (важно для Render Web Service)
        asyncio.create_task(start_keep_alive_server())

        # На всякий случай убираем вебхук и запускаем polling
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)

    except asyncio.CancelledError:
        logger.info("🛑 Задача отменена (CancelledError)")
    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал остановки")
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
