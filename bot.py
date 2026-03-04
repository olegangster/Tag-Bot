import asyncio
import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from handlers import router, queue_mgr

try:
    from keep_alive import start_keep_alive_server
    _has_keep_alive = True
except ImportError:
    _has_keep_alive = False
    logger.warning("keep_alive.py не найден")

load_dotenv()
load_dotenv(".env.txt")


async def _autostart_queue(bot: Bot):
    from media_processor import MediaProcessor
    has_pending = bool(queue_mgr.get_pending_queue())
    has_running = bool(queue_mgr._running_batch_items)
    if not has_pending and not has_running:
        logger.info("Очередь пуста — авто-старт не нужен")
        return
    logger.info(
        f"Авто-старт: {len(queue_mgr.get_pending_queue())} ожидающих + "
        f"{len(queue_mgr._running_batch_items)} возобновляемых"
    )
    media_proc = MediaProcessor(bot)
    started = await queue_mgr.start_queue(media_proc, bot)
    if started:
        logger.info("Очередь авто-запущена!")
    else:
        logger.warning("Авто-старт не удался")


async def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN не найден")
        return

    bot = Bot(token=bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("Бот запущен!")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        if _has_keep_alive:
            asyncio.create_task(start_keep_alive_server())
        await _autostart_queue(bot)
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Остановка")
    finally:
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
