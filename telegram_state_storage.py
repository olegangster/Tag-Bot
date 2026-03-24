"""
telegram_state_storage.py — Персистентное хранилище состояния очереди через Telegram

ПРОБЛЕМА: Render.com при деплое уничтожает все файлы контейнера.

РЕШЕНИЕ: Хранить queue + running_batches как JSON в одном Telegram-сообщении.
Telegram — внешний сервис, не зависит от файловой системы Render.

КАК РАБОТАЕТ:
1. При первом запуске бот отправляет «служебное» сообщение и выводит его
   message_id в лог. Сохрани это число как STATE_MSG_ID на Render.
2. При каждом изменении очереди — editMessageText этого сообщения (JSON).
3. При старте бота — читаем JSON из этого сообщения через forwardMessage.
4. Локальный файл tg_state_cache.json — быстрый кэш между save/load.

НАСТРОЙКА (добавь в .env.txt или Render Environment Variables):
    STORAGE_CHAT_ID=<твой_telegram_user_id>
    STATE_MSG_ID=<message_id>   ← появится в логах при первом запуске

ВАЖНО: STATE_MSG_ID нужно добавить один раз после первого запуска.
Без него очередь не будет восстанавливаться после рестарта.
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

_STATE_MARKER = "#BOT_STATE"
_MAX_TG_LEN = 3800  # Запас от лимита 4096


def _dt_serial(obj):
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = KYIV_TZ.localize(obj)
        return obj.isoformat()
    raise TypeError(f"Not serializable: {type(obj)}")


class TelegramStateStorage:
    """
    Хранит очередь и активные пакеты в одном Telegram-сообщении.
    """

    def __init__(self, bot, storage_chat_id: int, local_dir: Path = None):
        self.bot = bot
        self.storage_chat_id = storage_chat_id

        # message_id служебного сообщения
        self._state_msg_id: Optional[int] = self._read_msg_id_from_env()

        # Локальный кэш-файл
        if local_dir is None:
            render_cache = Path("/opt/render/project/.render")
            if render_cache.exists():
                local_dir = render_cache / "botdata"
                local_dir.mkdir(exist_ok=True)
            else:
                local_dir = Path(".")
        self.local_dir = local_dir
        self._cache_file = self.local_dir / "tg_state_cache.json"

        # In-memory кэш (актуальное состояние)
        self._mem_state: Optional[dict] = None
        self._initialized = False

    def _read_msg_id_from_env(self) -> Optional[int]:
        val = os.getenv("STATE_MSG_ID", "").strip()
        if val:
            try:
                return int(val)
            except ValueError:
                logger.warning(f"Некорректный STATE_MSG_ID={val!r}")
        return None

    # ─── Init ────────────────────────────────────────────────────────────────

    async def initialize(self):
        """Вызывается один раз при старте. Загружает состояние из Telegram."""
        if self._initialized:
            return
        self._initialized = True

        try:
            if self._state_msg_id:
                state = await self._fetch_from_telegram()
                if state is not None:
                    self._mem_state = state
                    self._write_cache(state)
                    logger.info(
                        f"✅ Загружено из Telegram msg_id={self._state_msg_id}: "
                        f"queue={len(state.get('queue', []))}, "
                        f"running={len(state.get('running_batches', {}))}"
                    )
                    return
                else:
                    logger.warning(
                        f"Не удалось прочитать msg_id={self._state_msg_id} из Telegram. "
                        f"Пробуем локальный кэш..."
                    )
            else:
                logger.warning(
                    "STATE_MSG_ID не задан — очередь не восстановится после рестарта. "
                    "Создаём новое служебное сообщение..."
                )

            # Пробуем локальный кэш
            cached = self._read_cache()
            if cached:
                self._mem_state = cached
                logger.info(f"📂 Загружено из локального кэша: queue={len(cached.get('queue', []))}")
                # Сразу пишем в Telegram чтобы создать/восстановить сообщение
                await self._ensure_state_message_exists()
                await self._push_to_telegram(cached)
            else:
                self._mem_state = {"queue": [], "running_batches": {}}
                await self._ensure_state_message_exists()

        except Exception as e:
            logger.error(f"❌ TelegramStateStorage.initialize(): {e}", exc_info=True)
            if self._mem_state is None:
                self._mem_state = {"queue": [], "running_batches": {}}

    async def _ensure_state_message_exists(self):
        """Создаёт служебное сообщение если его нет."""
        if self._state_msg_id:
            return
        try:
            msg = await self.bot.send_message(
                self.storage_chat_id,
                f"{_STATE_MARKER}\n{{}}",
                disable_notification=True,
            )
            self._state_msg_id = msg.message_id
            try:
                await self.bot.pin_chat_message(
                    self.storage_chat_id,
                    msg.message_id,
                    disable_notification=True,
                )
            except Exception:
                pass
            logger.info(
                f"\n{'='*50}\n"
                f"✅ Служебное сообщение создано!\n"
                f"   Добавь в Render Environment Variables:\n"
                f"   STATE_MSG_ID={self._state_msg_id}\n"
                f"{'='*50}"
            )
        except Exception as e:
            logger.error(f"Не удалось создать служебное сообщение: {e}")

    # ─── Telegram I/O ────────────────────────────────────────────────────────

    async def _fetch_from_telegram(self) -> Optional[dict]:
        """
        Читает JSON из служебного сообщения.
        Telegram Bot API не позволяет читать сообщения по ID напрямую.
        Используем forward: пересылаем сообщение себе, читаем текст, удаляем.
        """
        if not self._state_msg_id:
            return None
        try:
            fwd = await self.bot.forward_message(
                chat_id=self.storage_chat_id,
                from_chat_id=self.storage_chat_id,
                message_id=self._state_msg_id,
                disable_notification=True,
            )
            text = fwd.text or fwd.caption or ""
            try:
                await self.bot.delete_message(self.storage_chat_id, fwd.message_id)
            except Exception:
                pass

            if _STATE_MARKER not in text:
                logger.warning(f"Маркер {_STATE_MARKER!r} не найден в сообщении")
                return None

            json_str = text[text.index('\n') + 1:].strip()
            if not json_str or json_str == "{}":
                return {"queue": [], "running_batches": {}}

            return json.loads(json_str)

        except Exception as e:
            logger.warning(f"_fetch_from_telegram: {e}")
            return None

    async def _push_to_telegram(self, state: dict):
        """Записывает JSON в служебное сообщение через editMessageText."""
        if not self._state_msg_id:
            return

        try:
            json_str = json.dumps(
                state, ensure_ascii=False,
                separators=(',', ':'), default=_dt_serial
            )
            text = f"{_STATE_MARKER}\n{json_str}"

            if len(text) > _MAX_TG_LEN:
                # Очередь слишком большая — сохраняем только локально
                logger.warning(
                    f"⚠️ Состояние слишком большое ({len(text)} chars > {_MAX_TG_LEN}). "
                    f"Только локальный кэш."
                )
                return

            await self.bot.edit_message_text(
                chat_id=self.storage_chat_id,
                message_id=self._state_msg_id,
                text=text,
            )

        except Exception as e:
            err = str(e).lower()
            if "message is not modified" in err:
                pass  # Данные не изменились — ок
            elif "message to edit not found" in err or "message_id_invalid" in err:
                logger.warning(
                    f"Служебное сообщение {self._state_msg_id} удалено. "
                    f"Создаём новое..."
                )
                self._state_msg_id = None
                await self._ensure_state_message_exists()
                # Повторная попытка
                await self._push_to_telegram(state)
            else:
                logger.warning(f"_push_to_telegram: {e}")

    # ─── Local cache ─────────────────────────────────────────────────────────

    def _write_cache(self, state: dict):
        try:
            with open(self._cache_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2, default=_dt_serial)
        except Exception as e:
            logger.warning(f"Ошибка записи кэша: {e}")

    def _read_cache(self) -> Optional[dict]:
        if self._cache_file.exists():
            try:
                with open(self._cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Ошибка чтения кэша: {e}")
        return None

    # ─── Public API ───────────────────────────────────────────────────────────

    def _get_state(self) -> dict:
        if self._mem_state is None:
            self._mem_state = self._read_cache() or {"queue": [], "running_batches": {}}
        return self._mem_state

    async def _save_state(self, state: dict):
        self._mem_state = state
        self._write_cache(state)
        # Не блокируем основной поток
        asyncio.create_task(self._push_to_telegram(state))

    async def load_queue(self) -> list:
        return self._get_state().get("queue", [])

    async def save_queue(self, queue: list):
        state = self._get_state()
        state["queue"] = queue
        await self._save_state(state)

    async def load_running_batches(self) -> dict:
        return self._get_state().get("running_batches", {})

    async def save_running_batches(self, batches: dict):
        state = self._get_state()
        state["running_batches"] = batches
        await self._save_state(state)


# ─── Глобальный экземпляр ─────────────────────────────────────────────────────

_storage: Optional[TelegramStateStorage] = None


def get_storage() -> Optional[TelegramStateStorage]:
    return _storage


def init_storage(bot, storage_chat_id: int) -> TelegramStateStorage:
    global _storage
    _storage = TelegramStateStorage(bot, storage_chat_id)
    return _storage
