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
    logger.warning("keep_alive.py не знайдено")

load_dotenv()
load_dotenv(".env.txt")


async def _autostart_queue(bot: Bot):
    from media_processor import MediaProcessor
    media_proc = MediaProcessor(bot)

    # ✅ НОВОЕ: подключаем Telegram-persistent storage
    # Это загрузит актуальное состояние очереди из Telegram после деплоя
    await queue_mgr.setup(bot)

    started = await queue_mgr.start_queue(media_proc, bot)
    has_pending = bool(queue_mgr.get_pending_queue())
    has_running = bool(queue_mgr._running_batch_items)

    if has_running:
        logger.info(
            f"Авто-старт: {len(queue_mgr.get_pending_queue())} в очікуванні + "
            f"{len(queue_mgr._running_batch_items)} возобновляются"
        )
        logger.info("Очередь авто-запущена и восстанавливает активные пакеты!")
    elif has_pending:
        logger.info(f"Авто-старт: {len(queue_mgr.get_pending_queue())} елементів в очікуванні")
    else:
        logger.info("Цикл очереди запущен в режиме ожидания (очередь пуста)")


async def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN не знайдено")
        return

    bot = Bot(token=bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    # Видаляємо вебхук і чекаємо 2с щоб Telegram звільнив старий long-poll
    # Це вирішує TelegramConflictError при перезапуску на Render
    logger.info("Очищення старих сесій Telegram...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(2)
        logger.info("Сесію очищено")
    except Exception as e:
        logger.warning(f"delete_webhook: {e}")

    if _has_keep_alive:
        try:
            asyncio.create_task(start_keep_alive_server())
        except Exception as e:
            logger.error(f"keep_alive не запустився: {e}")

    await _autostart_queue(bot)
    logger.info("Бот запущено!")

    try:
        await dp.start_polling(bot)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Зупинка за сигналом")
    except Exception as e:
        logger.error(f"Polling впав: {e}")
    finally:
        logger.info("Завершення...")
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception:
            pass
        await bot.session.close()
        logger.info("Бот зупинено")


if __name__ == "__main__":
    asyncio.run(main())
