import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# logging ПЕРВЫМ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from handlers import router, queue_mgr

try:
    from keep_alive import start_keep_alive_server
    _has_keep_alive = True
except ImportError:
    _has_keep_alive = False
    logger.warning("⚠️ keep_alive.py не найден — /health endpoint отключён")

load_dotenv()
load_dotenv(".env.txt")


async def _autostart_queue(bot: Bot):
    """
    Авто-старт очереди после перезапуска Render.
    queue.json сохраняется, но running=False — посты зависают.
    Эта функция автоматически возобновляет публикацию.
    """
    from media_processor import MediaProcessor

    pending = queue_mgr.get_pending_queue()
    if not pending:
        logger.info("📭 Очередь пуста — авто-старт не нужен")
        return

    logger.info(f"🔄 После перезапуска найдено {len(pending)} эл. в очереди — запускаем!")
    media_proc = MediaProcessor(bot)
    started = await queue_mgr.start_queue(media_proc, bot)

    if started:
        logger.info("✅ Очередь авто-запущена после перезапуска!")
    else:
        logger.warning("⚠️ Авто-старт не удался")


async def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("❌ BOT_TOKEN не найден")
        return

    bot = Bot(token=bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("🚀 Бот запущен!")

    try:
        await bot.delete_webhook(drop_pending_updates=True)

        if _has_keep_alive:
            asyncio.create_task(start_keep_alive_server())

        # Авто-старт очереди — ОБЯЗАТЕЛЬНО перед polling
        await _autostart_queue(bot)

        await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("🛑 Остановка")
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
