"""
telegram_state_storage.py — Персистентне сховище стану черги через Telegram

ПРОБЛЕМА: Render.com при деплої знищує всі файли контейнера.
Навіть /opt/render/project/.render/ скидається при новому деплої.

РІШЕННЯ: Зберігати queue.json і running_batches.json як повідомлення
в Telegram (pinned message у чаті адміна). Telegram — це зовнішній
сервіс, який не залежить від файлової системи Render.

АРХІТЕКТУРА:
- Зберігаємо стан як JSON у pinned-повідомленні в STORAGE_CHAT_ID
- При старті — завантажуємо з Telegram, якщо файл порожній/відсутній
- При кожному оновленні — зберігаємо і в файл (кеш), і в Telegram
- Файл — швидкий локальний кеш, Telegram — надійний persistent backup

НАЛАШТУВАННЯ:
Додай у .env.txt:
    STORAGE_CHAT_ID=<твій_user_id або id приватного каналу>

Якщо STORAGE_CHAT_ID не задано — використовується перший admin з ADMIN_IDS.
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytz

logger = logging.getLogger(__name__)

KYIV_TZ = pytz.timezone('Europe/Kiev')

# Теги для ідентифікації повідомлень зі станом
_TAG_QUEUE = "🗃️ BOT_STATE:queue"
_TAG_RUNNING = "🗃️ BOT_STATE:running_batches"
_STATE_MARKER = "⚙️ BOT STATE STORAGE — НЕ ВИДАЛЯТИ"


def _dt_serial(obj):
    """JSON serializer для datetime."""
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = KYIV_TZ.localize(obj)
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class TelegramStateStorage:
    """
    Зберігає стан черги у Telegram як pinned-повідомлення.
    Працює як прозорий шар поверх файлового сховища.
    """

    def __init__(self, bot, storage_chat_id: int, local_dir: Path = None):
        self.bot = bot
        self.storage_chat_id = storage_chat_id

        # Локальний кеш (файли) — для швидкого читання
        if local_dir is None:
            render_cache = Path("/opt/render/project/.render")
            if render_cache.exists():
                local_dir = render_cache / "botdata"
                local_dir.mkdir(exist_ok=True)
            else:
                local_dir = Path(".")
        self.local_dir = local_dir

        # ID повідомлень зі станом (заповнюється при ініціалізації)
        self._queue_msg_id: Optional[int] = None
        self._running_msg_id: Optional[int] = None
        self._initialized = False

    async def initialize(self):
        """
        Знаходить або створює службові повідомлення в Telegram.
        Викликається один раз при старті бота.
        """
        if self._initialized:
            return

        try:
            # Шукаємо існуючі pinned-повідомлення зі станом
            await self._find_or_create_state_messages()
            self._initialized = True
            logger.info(
                f"✅ TelegramStateStorage ініціалізовано "
                f"(chat_id={self.storage_chat_id}, "
                f"queue_msg={self._queue_msg_id}, "
                f"running_msg={self._running_msg_id})"
            )
        except Exception as e:
            logger.error(f"❌ TelegramStateStorage ініціалізація провалилась: {e}")
            # Продовжуємо без Telegram storage — буде тільки файловий fallback
            self._initialized = True

    async def _find_or_create_state_messages(self):
        """Знаходить існуючі або створює нові повідомлення для зберігання стану."""
        # Отримуємо pinned message чату
        try:
            chat = await self.bot.get_chat(self.storage_chat_id)
            pinned = getattr(chat, 'pinned_message', None)
            if pinned and _STATE_MARKER in (pinned.text or ""):
                # Є pinned message — парсимо message_id звідти
                await self._parse_index_message(pinned.text)
                return
        except Exception as e:
            logger.warning(f"Не вдалось отримати pinned message: {e}")

        # Шукаємо через getUpdates / history неможливо через Bot API.
        # Тому просто створюємо нові повідомлення.
        await self._create_state_messages()

    async def _create_state_messages(self):
        """Створює нові службові повідомлення."""
        try:
            # Повідомлення для queue
            q_msg = await self.bot.send_message(
                self.storage_chat_id,
                f"{_TAG_QUEUE}\n[]"
            )
            self._queue_msg_id = q_msg.message_id

            # Повідомлення для running_batches
            r_msg = await self.bot.send_message(
                self.storage_chat_id,
                f"{_TAG_RUNNING}\n{{}}"
            )
            self._running_msg_id = r_msg.message_id

            # Індексне повідомлення з ID обох
            index_text = (
                f"{_STATE_MARKER}\n"
                f"queue_msg_id: {self._queue_msg_id}\n"
                f"running_msg_id: {self._running_msg_id}\n"
                f"created: {datetime.now(KYIV_TZ).isoformat()}"
            )
            index_msg = await self.bot.send_message(
                self.storage_chat_id, index_text
            )
            # Закріплюємо індексне повідомлення
            try:
                await self.bot.pin_chat_message(
                    self.storage_chat_id,
                    index_msg.message_id,
                    disable_notification=True
                )
            except Exception:
                pass  # Можливо немає прав — не критично

            logger.info(
                f"✅ Створено state messages: "
                f"queue={self._queue_msg_id}, running={self._running_msg_id}"
            )
        except Exception as e:
            logger.error(f"❌ Не вдалось створити state messages: {e}")

    async def _parse_index_message(self, text: str):
        """Парсить ID повідомлень з індексного повідомлення."""
        try:
            for line in text.split('\n'):
                if line.startswith('queue_msg_id:'):
                    self._queue_msg_id = int(line.split(':')[1].strip())
                elif line.startswith('running_msg_id:'):
                    self._running_msg_id = int(line.split(':')[1].strip())
            logger.info(
                f"📌 Знайдено state messages: "
                f"queue={self._queue_msg_id}, running={self._running_msg_id}"
            )
        except Exception as e:
            logger.warning(f"Помилка парсингу index message: {e}")
            await self._create_state_messages()

    # ─── Public API ───────────────────────────────────────────────────────────

    async def load_queue(self) -> list:
        """Завантажує чергу: спочатку з Telegram, fallback на файл."""
        data = await self._load_from_telegram(self._queue_msg_id, _TAG_QUEUE)
        if data is not None:
            self._save_local("queue.json", data)
            return data if isinstance(data, list) else []

        # Fallback: читаємо з файлу
        return self._load_local("queue.json", default=[])

    async def save_queue(self, queue: list):
        """Зберігає чергу в Telegram і локально."""
        self._save_local("queue.json", queue)
        await self._save_to_telegram(self._queue_msg_id, _TAG_QUEUE, queue)

    async def load_running_batches(self) -> dict:
        """Завантажує активні пакети: спочатку з Telegram, fallback на файл."""
        data = await self._load_from_telegram(
            self._running_msg_id, _TAG_RUNNING
        )
        if data is not None:
            self._save_local("running_batches.json", data)
            return data if isinstance(data, dict) else {}

        return self._load_local("running_batches.json", default={})

    async def save_running_batches(self, batches: dict):
        """Зберігає активні пакети в Telegram і локально."""
        self._save_local("running_batches.json", batches)
        await self._save_to_telegram(
            self._running_msg_id, _TAG_RUNNING, batches
        )

    # ─── Telegram I/O ─────────────────────────────────────────────────────────

    async def _load_from_telegram(self, msg_id: Optional[int], tag: str):
        """Завантажує JSON з Telegram повідомлення."""
        if not msg_id or not self.storage_chat_id:
            return None

        try:
            # Пересилаємо повідомлення самому собі щоб отримати текст
            # (forward + get_chat не дає текст, потрібен copyMessage або forwardMessage)
            # Використовуємо Bot API напряму через сесію
            from aiogram.methods import ForwardMessage
            fwd = await self.bot(
                ForwardMessage(
                    chat_id=self.storage_chat_id,
                    from_chat_id=self.storage_chat_id,
                    message_id=msg_id,
                )
            )
            text = fwd.text or ""
            # Видаляємо пересланий дубль
            try:
                await self.bot.delete_message(
                    self.storage_chat_id, fwd.message_id
                )
            except Exception:
                pass

            if tag not in text:
                logger.warning(f"Тег '{tag}' не знайдено в повідомленні {msg_id}")
                return None

            # JSON після першого \n
            json_str = text[text.index('\n') + 1:]
            return json.loads(json_str)

        except Exception as e:
            logger.warning(f"_load_from_telegram msg={msg_id}: {e}")
            return None

    async def _save_to_telegram(self, msg_id: Optional[int], tag: str, data):
        """Зберігає JSON у Telegram повідомлення через editMessageText."""
        if not msg_id or not self.storage_chat_id:
            return

        try:
            json_str = json.dumps(data, ensure_ascii=False, default=_dt_serial)
            new_text = f"{tag}\n{json_str}"

            # Telegram обмежує повідомлення 4096 символами
            if len(new_text) > 4096:
                # Стискаємо: видаляємо пробіли з JSON
                json_str = json.dumps(
                    data, ensure_ascii=False,
                    default=_dt_serial, separators=(',', ':')
                )
                new_text = f"{tag}\n{json_str}"

            if len(new_text) > 4096:
                # Дані занадто великі для одного повідомлення
                # Зберігаємо тільки локально і логуємо попередження
                logger.warning(
                    f"⚠️ Стан занадто великий для Telegram ({len(new_text)} chars). "
                    f"Зберігаємо тільки локально."
                )
                return

            await self.bot.edit_message_text(
                chat_id=self.storage_chat_id,
                message_id=msg_id,
                text=new_text,
            )
        except Exception as e:
            logger.warning(f"_save_to_telegram msg={msg_id}: {e}")

    # ─── Local file fallback ───────────────────────────────────────────────────

    def _local_path(self, filename: str) -> Path:
        return self.local_dir / filename

    def _load_local(self, filename: str, default=None):
        path = self._local_path(filename)
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Помилка читання {filename}: {e}")
        return default if default is not None else {}

    def _save_local(self, filename: str, data):
        path = self._local_path(filename)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False,
                          indent=2, default=_dt_serial)
        except Exception as e:
            logger.warning(f"Помилка запису {filename}: {e}")


# ─── Глобальний екземпляр ─────────────────────────────────────────────────────
# Ініціалізується в bot.py після створення bot instance

_storage: Optional[TelegramStateStorage] = None


def get_storage() -> Optional[TelegramStateStorage]:
    return _storage


def init_storage(bot, storage_chat_id: int) -> TelegramStateStorage:
    global _storage
    _storage = TelegramStateStorage(bot, storage_chat_id)
    return _storage
