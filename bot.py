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
    media_proc = MediaProcessor(bot)
    # ✅ Всегда запускаем цикл очереди — он будет ждать новых элементов в фоне.
    # Это необходимо чтобы после перезапуска очередь сразу принимала новые пакеты.
    started = await queue_mgr.start_queue(media_proc, bot)
    has_pending = bool(queue_mgr.get_pending_queue())
    has_running = bool(queue_mgr._running_batch_items)
    if has_running:
        logger.info(
            f"Авто-старт: {len(queue_mgr.get_pending_queue())} ожидающих + "
            f"{len(queue_mgr._running_batch_items)} возобновляемых"
        )
        logger.info("Очередь авто-запущена и восстанавливает активные пакеты!")
    elif has_pending:
        logger.info(f"Авто-старт: {len(queue_mgr.get_pending_queue())} ожидающих элементов")
    else:
        logger.info("Цикл очереди запущен в режиме ожидания (очередь пуста)")


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
