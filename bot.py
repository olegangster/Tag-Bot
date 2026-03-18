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

    # FIX: удаляем вебхук и ждём 2с чтобы Telegram освободил старый long-poll
    # Это решает TelegramConflictError при перезапуске на Render
    logger.info("Очистка старых сессий Telegram...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(2)
        logger.info("Сессия очищена")
    except Exception as e:
        logger.warning(f"delete_webhook: {e}")

    # FIX: запускаем keep_alive с try/except — краш keep_alive не убивает бота
    if _has_keep_alive:
        try:
            asyncio.create_task(start_keep_alive_server())
        except Exception as e:
            logger.error(f"keep_alive не запустился: {e}")

    await _autostart_queue(bot)
    logger.info("Бот запущен!")

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановка по сигналу")
    except Exception as e:
        logger.error(f"Polling упал: {e}")
    finally:
        logger.info("Завершение...")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
