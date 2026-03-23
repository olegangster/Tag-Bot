"""
telegram_state_storage.py — Персистентное хранилище состояния очереди через Telegram

ПРОБЛЕМА: Render.com при деплое уничтожает все файлы контейнера.
Даже /opt/render/project/.render/ сбрасывается при новом деплое.

РЕШЕНИЕ: Хранить queue.json и running_batches.json как сообщения
в Telegram (pinned message в чате админа). Telegram — это внешний
сервис, который не зависит от файловой системы Render.

АРХИТЕКТУРА:
- Сохраняем состояние как JSON в pinned-сообщении в STORAGE_CHAT_ID
- При старте — загружаем из Telegram, если файл пустой/отсутствует
- При каждом обновлении — сохраняем и в файл (кэш), и в Telegram
- Файл — быстрый локальный кэш, Telegram — надёжный persistent backup

НАСТРОЙКА:
Добавь в .env.txt:
    STORAGE_CHAT_ID=<твой_user_id или id приватного канала>

Если STORAGE_CHAT_ID не задан — используется первый admin из ADMIN_IDS.
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

# Теги для идентификации сообщений с состоянием
_TAG_QUEUE = "🗃️ BOT_STATE:queue"
_TAG_RUNNING = "🗃️ BOT_STATE:running_batches"
_STATE_MARKER = "⚙️ BOT STATE STORAGE — НЕ УДАЛЯТЬ"


def _dt_serial(obj):
    """JSON serializer для datetime."""
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = KYIV_TZ.localize(obj)
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class TelegramStateStorage:
    """
    Хранит состояние очереди в Telegram как pinned-сообщение.
    Работает как прозрачный слой поверх файлового хранилища.
    """

    def __init__(self, bot, storage_chat_id: int, local_dir: Path = None):
        self.bot = bot
        self.storage_chat_id = storage_chat_id

        # Локальный кэш (файлы) — для быстрого чтения
        if local_dir is None:
            render_cache = Path("/opt/render/project/.render")
            if render_cache.exists():
                local_dir = render_cache / "botdata"
                local_dir.mkdir(exist_ok=True)
            else:
                local_dir = Path(".")
        self.local_dir = local_dir

        # ID сообщений с состоянием (заполняется при инициализации)
        self._queue_msg_id: Optional[int] = None
        self._running_msg_id: Optional[int] = None
        self._initialized = False

    async def initialize(self):
        """
        Находит или создаёт служебные сообщения в Telegram.
        Вызывается один раз при старте бота.
        """
        if self._initialized:
            return

        try:
            # Ищем существующие pinned-сообщения с состоянием
            await self._find_or_create_state_messages()
            self._initialized = True
            logger.info(
                f"✅ TelegramStateStorage инициализировано "
                f"(chat_id={self.storage_chat_id}, "
                f"queue_msg={self._queue_msg_id}, "
                f"running_msg={self._running_msg_id})"
            )
        except Exception as e:
            logger.error(f"❌ TelegramStateStorage инициализация провалилась: {e}")
            # Продолжаем без Telegram storage — будет только файловый fallback
            self._initialized = True

    async def _find_or_create_state_messages(self):
        """Находит существующие или создаёт новые сообщения для хранения состояния."""
        # Получаем pinned message чата
        try:
            chat = await self.bot.get_chat(self.storage_chat_id)
            pinned = getattr(chat, 'pinned_message', None)
            if pinned and _STATE_MARKER in (pinned.text or ""):
                # Есть pinned message — парсим message_id оттуда
                await self._parse_index_message(pinned.text)
                return
        except Exception as e:
            logger.warning(f"Не удалось получить pinned message: {e}")

        # Поиск через getUpdates / history невозможен через Bot API.
        # Поэтому просто создаём новые сообщения.
        await self._create_state_messages()

    async def _create_state_messages(self):
        """Создаёт новые служебные сообщения."""
        try:
            # Сообщение для queue
            q_msg = await self.bot.send_message(
                self.storage_chat_id,
                f"{_TAG_QUEUE}\n[]"
            )
            self._queue_msg_id = q_msg.message_id

            # Сообщение для running_batches
            r_msg = await self.bot.send_message(
                self.storage_chat_id,
                f"{_TAG_RUNNING}\n{{}}"
            )
            self._running_msg_id = r_msg.message_id

            # Индексное сообщение с ID обоих
            index_text = (
                f"{_STATE_MARKER}\n"
                f"queue_msg_id: {self._queue_msg_id}\n"
                f"running_msg_id: {self._running_msg_id}\n"
                f"created: {datetime.now(KYIV_TZ).isoformat()}"
            )
            index_msg = await self.bot.send_message(
                self.storage_chat_id, index_text
            )
            # Закрепляем индексное сообщение
            try:
                await self.bot.pin_chat_message(
                    self.storage_chat_id,
                    index_msg.message_id,
                    disable_notification=True
                )
            except Exception:
                pass  # Возможно нет прав — не критично

            logger.info(
                f"✅ Созданы state messages: "
                f"queue={self._queue_msg_id}, running={self._running_msg_id}"
            )
        except Exception as e:
            logger.error(f"❌ Не удалось создать state messages: {e}")

    async def _parse_index_message(self, text: str):
        """Парсит ID сообщений из индексного сообщения."""
        try:
            for line in text.split('\n'):
                if line.startswith('queue_msg_id:'):
                    self._queue_msg_id = int(line.split(':')[1].strip())
                elif line.startswith('running_msg_id:'):
                    self._running_msg_id = int(line.split(':')[1].strip())
            logger.info(
                f"📌 Найдены state messages: "
                f"queue={self._queue_msg_id}, running={self._running_msg_id}"
            )
        except Exception as e:
            logger.warning(f"Ошибка парсинга index message: {e}")
            await self._create_state_messages()

    # ─── Public API ───────────────────────────────────────────────────────────

    async def load_queue(self) -> list:
        """Загружает очередь: сначала из Telegram, fallback на файл."""
        data = await self._load_from_telegram(self._queue_msg_id, _TAG_QUEUE)
        if data is not None:
            self._save_local("queue.json", data)
            return data if isinstance(data, list) else []

        # Fallback: читаем из файла
        return self._load_local("queue.json", default=[])

    async def save_queue(self, queue: list):
        """Сохраняет очередь в Telegram и локально."""
        self._save_local("queue.json", queue)
        await self._save_to_telegram(self._queue_msg_id, _TAG_QUEUE, queue)

    async def load_running_batches(self) -> dict:
        """Загружает активные пакеты: сначала из Telegram, fallback на файл."""
        data = await self._load_from_telegram(
            self._running_msg_id, _TAG_RUNNING
        )
        if data is not None:
            self._save_local("running_batches.json", data)
            return data if isinstance(data, dict) else {}

        return self._load_local("running_batches.json", default={})

    async def save_running_batches(self, batches: dict):
        """Сохраняет активные пакеты в Telegram и локально."""
        self._save_local("running_batches.json", batches)
        await self._save_to_telegram(
            self._running_msg_id, _TAG_RUNNING, batches
        )

    # ─── Telegram I/O ─────────────────────────────────────────────────────────

    async def _load_from_telegram(self, msg_id: Optional[int], tag: str):
        """Загружает JSON из Telegram сообщения."""
        if not msg_id or not self.storage_chat_id:
            return None

        try:
            # Пересылаем сообщение самому себе чтобы получить текст
            # (forward + get_chat не даёт текст, нужен copyMessage или forwardMessage)
            # Используем Bot API напрямую через сессию
            from aiogram.methods import ForwardMessage
            fwd = await self.bot(
                ForwardMessage(
                    chat_id=self.storage_chat_id,
                    from_chat_id=self.storage_chat_id,
                    message_id=msg_id,
                )
            )
            text = fwd.text or ""
            # Удаляем пересланный дубль
            try:
                await self.bot.delete_message(
                    self.storage_chat_id, fwd.message_id
                )
            except Exception:
                pass

            if tag not in text:
                logger.warning(f"Тег '{tag}' не найден в сообщении {msg_id}")
                return None

            # JSON после первого \n
            json_str = text[text.index('\n') + 1:]
            return json.loads(json_str)

        except Exception as e:
            logger.warning(f"_load_from_telegram msg={msg_id}: {e}")
            return None

    async def _save_to_telegram(self, msg_id: Optional[int], tag: str, data):
        """Сохраняет JSON в Telegram сообщение через editMessageText."""
        if not msg_id or not self.storage_chat_id:
            return

        try:
            json_str = json.dumps(data, ensure_ascii=False, default=_dt_serial)
            new_text = f"{tag}\n{json_str}"

            # Telegram ограничивает сообщения 4096 символами
            if len(new_text) > 4096:
                # Сжимаем: удаляем пробелы из JSON
                json_str = json.dumps(
                    data, ensure_ascii=False,
                    default=_dt_serial, separators=(',', ':')
                )
                new_text = f"{tag}\n{json_str}"

            if len(new_text) > 4096:
                # Данные слишком большие для одного сообщения
                # Сохраняем только локально и логируем предупреждение
                logger.warning(
                    f"⚠️ Состояние слишком большое для Telegram ({len(new_text)} chars). "
                    f"Сохраняем только локально."
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
                logger.warning(f"Ошибка чтения {filename}: {e}")
        return default if default is not None else {}

    def _save_local(self, filename: str, data):
        path = self._local_path(filename)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False,
                          indent=2, default=_dt_serial)
        except Exception as e:
            logger.warning(f"Ошибка записи {filename}: {e}")


# ─── Глобальный экземпляр ─────────────────────────────────────────────────────
# Инициализируется в bot.py после создания bot instance

_storage: Optional[TelegramStateStorage] = None


def get_storage() -> Optional[TelegramStateStorage]:
    return _storage


def init_storage(bot, storage_chat_id: int) -> TelegramStateStorage:
    global _storage
    _storage = TelegramStateStorage(bot, storage_chat_id)
    return _storage
