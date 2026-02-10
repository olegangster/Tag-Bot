import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from handlers import router
from keep_alive import start_keep_alive_server

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Загружаем .env (у тебя файл называется .env.txt)
load_dotenv(".env.txt")


async def main():
    """Главная функция запуска бота"""

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("❌ BOT_TOKEN не найден в .env.txt")
        return

    bot = Bot(token=bot_token)
    dp = Dispatcher(storage=MemoryStorage())

    # Подключаем роутеры
    dp.include_router(router)

    try:
        # Важно: если до этого был webhook — убираем
        await bot.delete_webhook(drop_pending_updates=True)

        # Важно: на Web Service Render ОБЯЗАН быть открыт порт
        # Поэтому поднимаем health-сервер и убеждаемся, что он запущен
        try:
            await start_keep_alive_server()
        except Exception:
            logger.exception("❌ Не удалось запустить health-сервер (/health)")
            raise

        logger.info("🚀 Бот запущен и готов к работе!")
        logger.info("✅ Health endpoint: /health (Render)")
        logger.info("📋 Для остановки нажмите Ctrl+C")

        # Запуск polling
        await dp.start_polling(bot)

    except asyncio.CancelledError:
        logger.warning("🛑 Задача отменена (CancelledError)")
        raise
    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал остановки (KeyboardInterrupt)")
    except Exception:
        logger.exception("🔥 Критическая ошибка при работе бота")
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
