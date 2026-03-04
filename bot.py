import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# ✅ logging ПЕРВЫМ
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
    ✅ ИСПРАВЛЕНО v3: Авто-старт очереди после перезапуска Render.

    Теперь проверяет ОБА источника:
    1. queue.json — ожидающие пакеты (как раньше)
    2. running_batches.json — пакеты которые УЖЕ публиковались до перезапуска

    Благодаря running_batches.json бот продолжает с места остановки,
    не публикуя заново то что уже было опубликовано.
    """
    from media_processor import MediaProcessor

    has_pending = bool(queue_mgr.get_pending_queue())  # без фильтра по user_id
    has_running = bool(queue_mgr._running_batch_items)  # загружены из running_batches.json

    if not has_pending and not has_running:
        logger.info("📭 Очередь пуста — авто-старт не нужен")
        return

    total = len(queue_mgr.get_pending_queue()) + len(queue_mgr._running_batch_items)
    logger.info(
        f"🔄 Авто-старт: "
        f"{len(queue_mgr.get_pending_queue())} ожидающих + "
        f"{len(queue_mgr._running_batch_items)} возобновляемых"
    )

    media_proc = MediaProcessor(bot)
    started = await queue_mgr.start_queue(media_proc, bot)

    if started:
        logger.info(f"✅ Очередь авто-запущена! Всего элементов: {total}")
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

        # ✅ Авто-старт очереди — ОБЯЗАТЕЛЬНО перед polling
        await _autostart_queue(bot)

        await dp.start_polling(bot)

    except KeyboardInterrupt:
        logger.info("🛑 Остановка")
    finally:
        await bot.session.close()
        logger.info("👋 Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
