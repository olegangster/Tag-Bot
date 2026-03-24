"""
queue_manager.py — менеджер очереди публикаций

✅ v4 — НЕУБИВАЕМАЯ ОЧЕРЕДЬ:

ПРОБЛЕМА v3: running_batches.json и queue.json хранились в файловой
системе Render.com, которая уничтожается при каждом деплое/перезапуске.
Код восстановления работал, но файлов уже не было.

РЕШЕНИЕ v4:
1. TelegramStateStorage — хранит состояние в Telegram сообщениях.
   Telegram — внешний сервис, не зависит от файловой системы Render.
2. При СТАРТЕ — загружаем из Telegram (всегда актуально после деплоя).
3. При КАЖДОМ СОХРАНЕНИИ — сохраняем и в файл (кэш), и в Telegram.
4. Если Telegram недоступен — fallback на локальный файл.
5. Публичный API QueueManager не изменился — handlers.py не требует изменений.

НАСТРОЙКА (добавь в .env.txt или Render Environment Variables):
    STORAGE_CHAT_ID=<твой_telegram_user_id>
    # или оставь пустым — тогда используется первый admin из ADMIN_IDS
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pytz

logger = logging.getLogger(__name__)

KYIV_TZ = pytz.timezone('Europe/Kiev')


def load_config() -> dict:
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _dt_to_str(dt) -> str:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = KYIV_TZ.localize(dt)
        return dt.isoformat()
    return str(dt)


def _str_to_dt(s) -> datetime:
    if isinstance(s, datetime):
        if s.tzinfo is None:
            return KYIV_TZ.localize(s)
        return s
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = KYIV_TZ.localize(dt)
        return dt
    except Exception:
        return datetime.now(KYIV_TZ)


def _deserialize_schedule(data: list) -> list:
    """Конвертирует строки ISO в datetime во всех элементах списка."""
    result = []
    for item in data:
        ic = dict(item)
        if "schedule" in ic:
            ic["schedule"] = [_str_to_dt(s) for s in ic["schedule"]]
        result.append(ic)
    return result


def _deserialize_running(data: dict) -> dict:
    """Конвертирует строки ISO в datetime в running_batches."""
    result = {}
    for batch_id, item in data.items():
        ic = dict(item)
        if "schedule" in ic:
            ic["schedule"] = [_str_to_dt(s) for s in ic["schedule"]]
        result[batch_id] = ic
    return result


class QueueManager:
    def __init__(self, filepath: str = "queue.json"):
        # Локальный fallback-путь
        render_cache = Path("/opt/render/project/.render")
        if render_cache.exists():
            data_dir = render_cache / "botdata"
            data_dir.mkdir(exist_ok=True)
            self.filepath = data_dir / filepath
            self.running_filepath = data_dir / "running_batches.json"
        else:
            self.filepath = Path(filepath)
            self.running_filepath = Path("running_batches.json")

        self.previews_dir = self.filepath.parent / "previews"
        self.previews_dir.mkdir(exist_ok=True)

        # Telegram storage (инициализируется асинхронно через setup())
        self._tg_storage = None

        # Состояние в памяти
        self._queue: List[Dict] = self._load_queue_sync()
        self.running: bool = False
        self.interval: int = 60
        self._task: Optional[asyncio.Task] = None
        self._active_id: Optional[str] = None
        self._active_progress: str = ""
        self._running_batch_items: Dict[str, Dict] = self._load_running_batches_sync()
        self.batch_service = None

    # ─────────────────── Async setup (Telegram storage) ─────────────────────

    async def setup(self, bot):
        """
        ✅ НОВЫЙ МЕТОД: Подключает Telegram storage и подгружает актуальное состояние.
        Вызывать в bot.py после создания bot instance, до start_polling.
        """
        try:
            from telegram_state_storage import init_storage, TelegramStateStorage

            storage_chat_id = self._get_storage_chat_id()
            if not storage_chat_id:
                logger.warning(
                    "⚠️ STORAGE_CHAT_ID не задан — очередь сохраняется только локально. "
                    "Добавь STORAGE_CHAT_ID в .env.txt или переменные Render для неубиваемой очереди."
                )
                return

            self._tg_storage = init_storage(bot, storage_chat_id)
            await self._tg_storage.initialize()

            # ✅ Загружаем актуальное состояние из Telegram (всегда свежее после деплоя)
            queue_data = await self._tg_storage.load_queue()
            if queue_data is not None:
                self._queue = _deserialize_schedule(queue_data)
                logger.info(f"📥 Очередь загружена из Telegram: {len(self._queue)} элементов")

            running_data = await self._tg_storage.load_running_batches()
            if running_data is not None:
                self._running_batch_items = _deserialize_running(running_data)
                if self._running_batch_items:
                    logger.info(
                        f"🔄 Активные пакеты восстановлены из Telegram: "
                        f"{len(self._running_batch_items)}"
                    )

        except ImportError:
            logger.warning(
                "telegram_state_storage.py не найден — "
                "очередь сохраняется только локально."
            )
        except Exception as e:
            logger.error(f"❌ setup() ошибка: {e}")

    def _get_storage_chat_id(self) -> Optional[int]:
        """Возвращает chat_id для хранения состояния."""
        # Сначала явно заданный STORAGE_CHAT_ID
        val = os.getenv("STORAGE_CHAT_ID", "").strip()
        if val:
            try:
                return int(val)
            except ValueError:
                pass

        # Fallback: первый admin из ADMIN_IDS
        admin_ids = os.getenv("ADMIN_IDS", "").strip()
        if admin_ids:
            first = admin_ids.split(",")[0].strip()
            try:
                return int(first)
            except ValueError:
                pass

        return None

    # ─────────────────── Sync file I/O (local fallback) ─────────────────────

    def _load_queue_sync(self) -> List[Dict]:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result = _deserialize_schedule(data)
                logger.info(f"📂 queue.json (локально): {len(result)} элементов")
                return result
            except Exception as e:
                logger.error(f"Ошибка чтения queue.json: {e}")
        return []

    def _load_running_batches_sync(self) -> Dict[str, Dict]:
        if self.running_filepath.exists():
            try:
                with open(self.running_filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result = _deserialize_running(data)
                if result:
                    logger.info(f"🔄 running_batches.json (локально): {len(result)}")
                return result
            except Exception as e:
                logger.error(f"Ошибка чтения running_batches.json: {e}")
        return {}

    def _save_queue_local(self):
        try:
            data = []
            for item in self._queue:
                ic = dict(item)
                if "schedule" in ic:
                    ic["schedule"] = [_dt_to_str(s) for s in ic["schedule"]]
                data.append(ic)
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения queue.json: {e}")

    def _save_running_local(self):
        try:
            data = {}
            for batch_id, item in self._running_batch_items.items():
                ic = dict(item)
                if "schedule" in ic:
                    ic["schedule"] = [_dt_to_str(s) for s in ic["schedule"]]
                data[batch_id] = ic
            with open(self.running_filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения running_batches.json: {e}")

    # ─────────────────── Async save (Telegram + local) ──────────────────────

    def _save_queue(self):
        """Синхронное сохранение локально + запускает async сохранение в Telegram."""
        self._save_queue_local()
        if self._tg_storage:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._save_queue_to_tg())
            except RuntimeError:
                pass  # Event loop не запущен — только локальное сохранение

    def _save_running_batches(self):
        """Синхронное сохранение локально + запускает async сохранение в Telegram."""
        self._save_running_local()
        if self._tg_storage:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._save_running_to_tg())
            except RuntimeError:
                pass  # Event loop не запущен — только локальное сохранение

    async def _save_queue_to_tg(self):
        """Сохраняет очередь в Telegram (вызывается как task)."""
        try:
            data = []
            for item in self._queue:
                ic = dict(item)
                if "schedule" in ic:
                    ic["schedule"] = [_dt_to_str(s) for s in ic["schedule"]]
                data.append(ic)
            await self._tg_storage.save_queue(data)
        except Exception as e:
            logger.warning(f"Ошибка сохранения очереди в Telegram: {e}")

    async def _save_running_to_tg(self):
        """Сохраняет running_batches в Telegram (вызывается как task)."""
        try:
            data = {}
            for batch_id, item in self._running_batch_items.items():
                ic = dict(item)
                if "schedule" in ic:
                    ic["schedule"] = [_dt_to_str(s) for s in ic["schedule"]]
                data[batch_id] = ic
            await self._tg_storage.save_running_batches(data)
        except Exception as e:
            logger.warning(f"Ошибка сохранения running_batches в Telegram: {e}")

    # Алиас для обратной совместимости
    def _save(self):
        self._save_queue()

    def _remove_running_batch(self, batch_id: str):
        if batch_id in self._running_batch_items:
            del self._running_batch_items[batch_id]
        self._save_running_batches()
        try:
            import shutil
            preview_batch_dir = self.previews_dir / batch_id
            if preview_batch_dir.exists():
                shutil.rmtree(preview_batch_dir)
        except Exception as e:
            logger.warning(f"Не удалось удалить превью пакета {batch_id}: {e}")
        logger.info(f"✅ Пакет {batch_id} завершён и удалён из активных")

    # ─────────────────────── Add items ───────────────────────────────────────

    def add_to_queue(self, user_id, name, category_key, category_data, platform_id,
                     media_files, selected_hashtags, description="", topic=None,
                     is_comic=False, comic_link="", comic_title="") -> str:
        item_id = str(uuid.uuid4())[:8]
        self._queue.append({
            "id": item_id, "type": "single", "user_id": user_id, "name": name,
            "category_key": category_key, "category_data": category_data,
            "platform_id": platform_id, "media_files": media_files,
            "selected_hashtags": selected_hashtags, "description": description,
            "topic": topic, "is_comic": is_comic,
            "comic_link": comic_link, "comic_title": comic_title,
        })
        self._save_queue()
        return item_id

    def add_batch_to_queue(self, user_id, category_data, category_key, platform_id,
                           posts, schedule, topic=None, name="Пакет",
                           time_period="hour", time_value=1, shuffle=False,
                           stars_price=None, selected_hashtags=None,
                           selected_dates=None, selected_times=None,
                           is_comic=False, comic_link="", comic_title="", **kwargs) -> str:
        batch_id = str(uuid.uuid4())[:8]
        preview_files = []

        self._queue.append({
            "id": batch_id, "type": "batch", "user_id": user_id,
            "name": name, "category_key": category_key,
            "category_data": category_data, "platform_id": platform_id,
            "topic": topic, "posts": posts, "schedule": list(schedule),
            "selected_hashtags": selected_hashtags or [], "time_period": time_period,
            "time_value": time_value, "shuffle": shuffle,
            "stars_price": stars_price,
            "selected_dates": selected_dates or [],
            "selected_times": selected_times or [],
            "published_count": 0,
            "is_comic": is_comic,
            "comic_link": comic_link,
            "comic_title": comic_title,
            "preview_files": preview_files,
            "files_per_post": len(posts[0]) if posts else 1,
            "total_posts_count": len(posts),
        })
        self._save_queue()
        return batch_id

    def merge_batch_into_active(self, user_id, category_data, category_key, platform_id,
                                posts, schedule, topic=None, name="Пакет",
                                time_period="hour", time_value=1, stars_price=None) -> str:
        active_batches = [bid for bid, item in self._running_batch_items.items()
                          if item.get("user_id") == user_id]
        if not active_batches:
            return self.add_batch_to_queue(
                user_id=user_id, category_data=category_data,
                category_key=category_key, platform_id=platform_id,
                posts=posts, schedule=schedule, topic=topic, name=name,
                time_period=time_period, time_value=time_value,
                stars_price=stars_price,
            )
        batch_id = active_batches[0]
        item = self._running_batch_items[batch_id]
        item["posts"].extend(posts)
        item["schedule"].extend(schedule)
        self._save_running_batches()
        logger.info(f"✅ Добавлено {len(posts)} постов в активный пакет {batch_id}")
        return batch_id

    # ─────────────────────── Queue access ────────────────────────────────────

    def get_queue(self, user_id: int = None) -> List[Dict]:
        result = []
        for batch_id, item in self._running_batch_items.items():
            if user_id is None or item.get("user_id") == user_id:
                result.append(dict(item))
        for item in self._queue:
            if user_id is None or item.get("user_id") == user_id:
                result.append(dict(item))
        return result

    def get_pending_queue(self, user_id: int = None) -> List[Dict]:
        if user_id is None:
            return list(self._queue)
        return [item for item in self._queue if item.get("user_id") == user_id]

    def get_pending_queue_indices(self, user_id: int) -> List[int]:
        return [i for i, item in enumerate(self._queue) if item.get("user_id") == user_id]

    def get_running_batch_ids(self, user_id: int = None) -> set:
        if user_id is None:
            return set(self._running_batch_items.keys())
        return {bid for bid, item in self._running_batch_items.items()
                if item.get("user_id") == user_id}

    def get_running_batch(self, batch_id: str) -> Optional[Dict]:
        return self._running_batch_items.get(batch_id)

    def is_batch_active(self) -> bool:
        return bool(self._running_batch_items)

    def is_batch_active_for_category(self, category_key: str = None, topic: str = None) -> bool:
        for bid, item in self._running_batch_items.items():
            if category_key and item.get("category_key") == category_key:
                return True
            if topic and item.get("topic") == topic:
                return True
        if self._queue and self._queue[0].get("type") == "batch":
            active = self._queue[0]
            if category_key and active.get("category_key") == category_key:
                return True
            if topic and active.get("topic") == topic:
                return True
        return False

    def get_active_info(self) -> Optional[Dict]:
        if self._active_id and self._queue:
            item = self._queue[0]
            if item.get("id") == self._active_id:
                return item
        return None

    # ─────────────────────── Mutate queue ────────────────────────────────────

    def remove_from_queue(self, index: int, user_id: int = None) -> bool:
        if user_id is not None:
            user_indices = self.get_pending_queue_indices(user_id)
            if index >= len(user_indices):
                return False
            real_index = user_indices[index]
        else:
            real_index = index

        if 0 <= real_index < len(self._queue):
            item = self._queue[real_index]
            if self.running and real_index == 0 and self._active_id == item.get("id"):
                return False
            self._queue.pop(real_index)
            if item.get("type") == "batch" and self.batch_service:
                if item.get("id"):
                    self.batch_service.cancel_batch_tasks(item["id"])
            self._save_queue()
            logger.info(f"Удалено из очереди: {item.get('name')}")
            return True
        return False

    def clear_queue(self, user_id: int = None, skip_active: bool = True):
        if user_id is not None:
            active_id = self._active_id if (skip_active and self.running) else None
            self._queue = [
                item for item in self._queue
                if item.get("user_id") != user_id or item.get("id") == active_id
            ]
        elif skip_active and self.running and self._queue:
            self._queue = [self._queue[0]]
        else:
            self._queue.clear()
        self._save_queue()

    def set_interval(self, minutes: int):
        self.interval = max(1, int(minutes))

    # ─────────────────────── Status ──────────────────────────────────────────

    def get_status(self, user_id: int = None) -> Dict:
        user_pending = self.get_pending_queue(user_id)
        user_running = {
            bid: item for bid, item in self._running_batch_items.items()
            if user_id is None or item.get("user_id") == user_id
        }
        return {
            "running": self.running,
            "pending": len(user_pending),
            "interval": self.interval,
            "active_progress": self._active_progress,
            "running_batches": user_running,
        }

    def get_status_text(self, user_id: int = None) -> str:
        s = self.get_status(user_id)
        icon = "🟢 Активна" if s["running"] else "🔴 Остановлена"
        # ✅ Показываем статус persistence
        tg_icon = "☁️" if self._tg_storage else "💾"
        text = f"📥 <b>Очередь публикаций</b> {tg_icon}\n\nСтатус: <b>{icon}</b>\n"

        running_batches = s.get("running_batches", {})
        if s["running"] and running_batches:
            text += f"\n<b>▶️ Публикуется сейчас ({len(running_batches)}):</b>\n"
            for batch_id, item in running_batches.items():
                cat = item.get("category_data", {})
                cat_name = f"{cat.get('emoji', '')} {cat.get('name', '—')}"
                published = item.get("published_count", 0)
                total = len(item.get("posts", []))
                text += f"• {item.get('name', batch_id)} [{cat_name}]: <b>{published}/{total}</b>\n"
        elif s["running"] and s["active_progress"]:
            text += f"▶️ Публикуется: <i>{s['active_progress']}</i>\n"

        text += f"\n📋 В очереди: <b>{s['pending']}</b>\n"
        text += f"⏱ Интервал: <b>{s['interval']} мин</b>\n"

        if not self._tg_storage:
            text += "\n⚠️ <i>Добавь STORAGE_CHAT_ID для неубиваемой очереди</i>"

        if running_batches:
            text += "\n\n💡 Нажми <b>📊 Прогресс</b> для деталей"

        return text

    def get_running_batch_detail_text(self, batch_id: str) -> str:
        item = self._running_batch_items.get(batch_id)
        if not item:
            return "⚠️ Пакет уже завершён или не найден"

        cat = item.get("category_data", {})
        cat_name = f"{cat.get('emoji', '')} {cat.get('name', '—')}"
        published_count = item.get("published_count", 0)
        published_log = item.get("published_log", [])
        schedule = item.get("schedule", [])
        total = len(item.get("posts", []))

        text = f"📦 <b>{item.get('name', batch_id)}</b>\n"
        text += f"📁 Категория: {cat_name}\n"
        text += f"📊 Прогресс: <b>{published_count}/{total}</b>\n\n"

        if published_log:
            text += "✅ <b>Уже опубликовано:</b>\n"
            for ts in published_log[-10:]:
                try:
                    dt = _str_to_dt(ts)
                    dt_kyiv = dt.astimezone(KYIV_TZ)
                    text += f"  • {dt_kyiv.strftime('%d.%m %H:%M')}\n"
                except Exception:
                    text += f"  • {ts}\n"
            text += "\n"

        remaining_schedule = schedule[published_count:]
        if remaining_schedule:
            text += "⏳ <b>Запланировано:</b>\n"
            for s in remaining_schedule[:10]:
                try:
                    dt = _str_to_dt(s)
                    dt_kyiv = dt.astimezone(KYIV_TZ)
                    text += f"  • {dt_kyiv.strftime('%d.%m %H:%M')}\n"
                except Exception:
                    text += f"  • {s}\n"
            if len(remaining_schedule) > 10:
                text += f"  ... и ещё {len(remaining_schedule) - 10}\n"

        return text

    def get_all_progress_text(self, user_id: int = None) -> str:
        items = {
            bid: item for bid, item in self._running_batch_items.items()
            if user_id is None or item.get("user_id") == user_id
        }
        if not items:
            return "▶️ Нет активных пакетов"
        text = ""
        for batch_id in items:
            text += self.get_running_batch_detail_text(batch_id)
            text += "\n" + "─" * 25 + "\n\n"
        return text.rstrip()

    # ─────────────────────── Queue control ───────────────────────────────────

    async def start_queue(self, media_processor, bot) -> bool:
        if self._task and not self._task.done():
            self.running = True
            return True

        from batch_service import BatchPostingService
        self.batch_service = BatchPostingService(media_processor, bot)

        self.running = True
        self._task = asyncio.create_task(
            self._process_queue_loop(media_processor, bot)
        )
        logger.info("▶️ Очередь запущена")
        return True

    async def stop_queue(self):
        self.running = False
        self._active_id = None
        self._active_progress = ""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("⏹ Очередь остановлена")

    # ─────────────────────── Queue loop ──────────────────────────────────────

    async def _process_queue_loop(self, media_processor, bot):
        logger.info("▶️ Цикл очереди запущен")
        batch_tasks: dict = {}

        # Восстановление прерванных пакетов после рестарта
        for batch_id, item in list(self._running_batch_items.items()):
            if batch_id not in batch_tasks:
                logger.info(
                    f"🔄 Восстанавливаем пакет '{item.get('name')}' "
                    f"(опубликовано: {item.get('published_count', 0)}/{len(item.get('posts', []))})"
                )
                task = asyncio.create_task(
                    self._publish_batch(batch_id, item, media_processor, bot)
                )
                batch_tasks[batch_id] = task

        while self.running:
            for bid in list(batch_tasks.keys()):
                if batch_tasks[bid].done():
                    del batch_tasks[bid]

            if not self._queue:
                await asyncio.sleep(2)
                continue

            item = self._queue[0]
            self._active_id = item["id"]

            try:
                if item["type"] == "batch":
                    logger.info(f"🚀 Запуск пакета '{item.get('name')}'")

                    _raw_posts = item.get("posts", [])
                    _media_list = []
                    for _post in _raw_posts:
                        for _entry in (_post if isinstance(_post, (list, tuple)) else []):
                            if isinstance(_entry, (list, tuple)) and len(_entry) >= 2:
                                _mtype, _fpath = _entry[0], _entry[1]
                                _media_list.append({"type": _mtype, "path": str(_fpath)})

                    running_item = {
                        **{k: v for k, v in item.items() if k != "schedule"},
                        "schedule": list(item.get("schedule", [])),
                        "_is_running": True,
                        "published_count": item.get("published_count", 0),
                        "published_log": item.get("published_log", []),
                        "media_list": _media_list,
                        "files_per_post": len(_raw_posts[0]) if _raw_posts else 1,
                        "total_posts_count": len(_raw_posts),
                    }
                    self._running_batch_items[item["id"]] = running_item
                    self._save_running_batches()  # ✅ Сохраняем до удаления из очереди

                    task = asyncio.create_task(
                        self._publish_batch(item["id"], running_item, media_processor, bot)
                    )
                    batch_tasks[item["id"]] = task

                    self._queue.pop(0)
                    self._save_queue()

                    self._active_id = None
                    self._active_progress = ""
                    continue

                else:
                    await self._publish_single(item, media_processor, bot)
                    if self._queue and self._queue[0].get("id") == item["id"]:
                        self._queue.pop(0)
                        self._save_queue()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ошибка публикации: {e}")
                if self._queue and self._queue[0].get("id") == item.get("id"):
                    self._queue.pop(0)
                    self._save_queue()

            self._active_id = None
            self._active_progress = ""

            if self._queue and self.running:
                await asyncio.sleep(self.interval * 60)

        remaining = {bid: t for bid, t in batch_tasks.items() if not t.done()}
        if remaining:
            logger.info(f"⏳ Ожидание завершения {len(remaining)} активных пакетов...")
            for batch_id, task in remaining.items():
                try:
                    await asyncio.wait_for(task, timeout=10.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
                    logger.warning(f"Пакет {batch_id} не завершился: {e}")
                    task.cancel()

        self._active_id = None
        self._active_progress = ""
        self.running = False
        logger.info("✅ Цикл очереди завершён")

    # ─────────────────────── Publish helpers ─────────────────────────────────

    async def _publish_single(self, item: Dict, media_processor, bot):
        category_data = item["category_data"]
        platform_id = item["platform_id"]
        topic = item.get("topic")

        config = load_config()
        platform_cfg = config.get("platforms", {}).get(platform_id, {})
        chat_id = int(platform_cfg.get("chat_id", 0))
        thread_id = self._resolve_thread(platform_cfg, topic)

        if item.get("is_comic"):
            invite_link = platform_cfg.get("invite_link", "https://t.me/your_channel")
            platform_type = platform_cfg.get("type", "group")
            referral_bot = platform_cfg.get("referral_bot", "@Tag_Referral_bot")
            comic_link = item.get("comic_link", "")
            comic_title = item.get("comic_title", "")
            caption = (
                f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}\n\n"
                f"{category_data.get('emoji_row', '')}\n\n"
                f'📖 <b><u><a href="{comic_link}">{comic_title}</a></u></b>\n\n'
                f"{category_data.get('emoji_row', '')}\n\n"
            )
            if platform_type == "channel":
                caption += f"Получить бесплатный доступ к привату ➡️ {referral_bot}\n"
            caption += f"↘️⬇️↙️\n"
            caption += f'<a href="{invite_link}">Ссылка для друга</a>'
            kwargs = dict(chat_id=chat_id, text=caption, parse_mode="HTML")
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_message(**kwargs)
        else:
            media_files = item.get("media_files", [])
            media_files.sort(key=lambda x: x[3] if len(x) > 3 else 0)
            media_to_process = [(m[0], m[1]) for m in media_files]
            processed = await media_processor.process_batch(media_to_process)
            if not processed:
                return
            caption = self._format_caption(
                category_data, item.get("selected_hashtags", []), platform_id
            )
            if len(processed) == 1:
                await media_processor.send_single_media(
                    chat_id, processed[0][0], processed[0][1], caption, thread_id
                )
            else:
                await media_processor.send_media_group(
                    chat_id, processed, caption, thread_id
                )
            media_processor.cleanup_specific([p for _, p in processed])
        logger.info(f"✅ '{item.get('name')}' опубликовано")

    async def _publish_batch(self, batch_id: str, item: Dict, media_processor, bot):
        posts = item.get("posts", [])
        schedule = item.get("schedule", [])
        category_data = item["category_data"]
        platform_id = item["platform_id"]
        topic = item.get("topic")
        batch_name = item.get("name", "Пакет")
        stars_price = item.get("stars_price")
        total = len(posts)

        start_from = item.get("published_count", 0)
        if start_from > 0:
            logger.info(f"🔄 Пакет '{batch_name}': продолжаем с поста {start_from + 1}/{total}")

        config = load_config()
        platform_cfg = config.get("platforms", {}).get(platform_id, {})
        chat_id = int(platform_cfg.get("chat_id", 0))
        thread_id = self._resolve_thread(platform_cfg, topic)

        is_comic = item.get("is_comic", False)
        comic_link = item.get("comic_link", "")
        comic_title = item.get("comic_title", "")

        if is_comic and comic_link and comic_title:
            caption = self._format_comic_caption(
                category_data, comic_title, comic_link,
                item.get("selected_hashtags", []), platform_id
            )
        else:
            caption = self._format_caption(
                category_data, item.get("selected_hashtags", []), platform_id
            )

        try:
            for i in range(start_from, total):
                if not self.running:
                    break

                post_data = posts[i]

                if batch_id in self._running_batch_items:
                    self._running_batch_items[batch_id]["published_count"] = i

                if i < len(schedule):
                    sched_time = schedule[i]
                    if isinstance(sched_time, str):
                        sched_time = _str_to_dt(sched_time)

                    if isinstance(sched_time, datetime):
                        if sched_time.tzinfo is None:
                            sched_time = KYIV_TZ.localize(sched_time)
                        now_aware = datetime.now(KYIV_TZ)
                        wait_sec = (sched_time - now_aware).total_seconds()

                        if wait_sec > 0:
                            logger.info(
                                f"⏳ '{batch_name}' пост {i+1}/{total} "
                                f"через {int(wait_sec)}с ({sched_time.strftime('%d.%m %H:%M')})"
                            )
                            while True:
                                if not self.running:
                                    break
                                remaining = (sched_time - datetime.now(KYIV_TZ)).total_seconds()
                                if remaining <= 0:
                                    break
                                await asyncio.sleep(min(remaining, 5.0))
                        elif wait_sec < -30:
                            missed_mins = int(abs(wait_sec) / 60)
                            logger.info(
                                f"⚡ '{batch_name}' пост {i+1}/{total}: "
                                f"опоздание {missed_mins} мин — публикуем сразу"
                            )
                            if i > start_from:
                                await asyncio.sleep(3)

                if not self.running:
                    break

                try:
                    if not post_data:
                        logger.warning(f"❌ '{batch_name}' пост {i+1}: пустые данные")
                        continue

                    first = post_data[0]
                    if isinstance(first, (list, tuple)) and len(first) >= 2:
                        media_to_process = [(str(m[0]), str(m[1])) for m in post_data]
                    elif isinstance(first, str) and len(post_data) >= 2:
                        media_to_process = [(str(post_data[0]), str(post_data[1]))]
                    else:
                        logger.warning(
                            f"❌ '{batch_name}' пост {i+1}: неизвестный формат: {post_data!r}"
                        )
                        continue

                    processed = await media_processor.process_batch(media_to_process)
                    if not processed:
                        logger.warning(f"❌ '{batch_name}' пост {i+1}: не удалось обработать медиа")
                        continue

                    if stars_price and stars_price > 0:
                        await self._send_stars_post(
                            bot=bot, chat_id=chat_id, thread_id=thread_id,
                            processed=processed, caption=caption,
                            stars_price=stars_price, media_processor=media_processor
                        )
                    else:
                        if len(processed) == 1:
                            await media_processor.send_single_media(
                                chat_id, processed[0][0], processed[0][1], caption, thread_id
                            )
                        else:
                            await media_processor.send_media_group(
                                chat_id, processed, caption, thread_id
                            )

                    media_processor.cleanup_specific([p for _, p in processed])

                    # ✅ Сохраняем прогресс после каждого поста (и в TG, и в файл)
                    published_at = datetime.now(KYIV_TZ).isoformat()
                    if batch_id in self._running_batch_items:
                        self._running_batch_items[batch_id]["published_count"] = i + 1
                        self._running_batch_items[batch_id].setdefault(
                            "published_log", []
                        ).append(published_at)
                        fpp = self._running_batch_items[batch_id].get("files_per_post", 1)
                        ml = self._running_batch_items[batch_id].get("media_list", [])
                        for _fi in range(min(fpp, len(ml))):
                            if not ml[_fi].get("published"):
                                ml[_fi]["published"] = True
                                break
                        self._save_running_batches()  # ✅ В Telegram + файл

                    try:
                        from keep_alive import notify_site_post_published
                        asyncio.create_task(
                            notify_site_post_published(
                                user_id=item.get("user_id", 0), count=1
                            )
                        )
                    except Exception:
                        pass

                    logger.info(f"✅ '{batch_name}': {i+1}/{total}")

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Ошибка поста {i+1} в пакете '{batch_name}': {e}")
                    continue

        except asyncio.CancelledError:
            logger.warning(f"Пакет '{batch_name}' отменён")
            # НЕ удаляем — при следующем старте восстановим
            return
        except Exception as e:
            logger.error(f"Критическая ошибка пакета '{batch_name}': {e}")
            self._remove_running_batch(batch_id)
            return

        self._remove_running_batch(batch_id)
        logger.info(f"🎉 Пакет '{batch_name}' полностью завершён ({total} постов)")

    async def _send_stars_post(self, bot, chat_id, thread_id, processed, caption,
                               stars_price, media_processor):
        from aiogram.types import LabeledPrice
        try:
            if len(processed) == 1:
                await media_processor.send_single_media(
                    chat_id, processed[0][0], processed[0][1], None, thread_id
                )
            else:
                await media_processor.send_media_group(chat_id, processed, None, thread_id)

            kwargs = dict(
                chat_id=chat_id,
                title="Эксклюзивный контент",
                description=caption[:255] if caption else "Эксклюзивный контент",
                payload="stars_post",
                currency="XTR",
                prices=[LabeledPrice(label="Звёзды", amount=stars_price)],
            )
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_invoice(**kwargs)
        except Exception as e:
            logger.error(f"Ошибка поста за звёзды: {e}")
            if len(processed) == 1:
                await media_processor.send_single_media(
                    chat_id, processed[0][0], processed[0][1], caption, thread_id
                )
            else:
                await media_processor.send_media_group(chat_id, processed, caption, thread_id)

    def _resolve_thread(self, platform_cfg: Dict, topic: Optional[str]) -> Optional[int]:
        if not topic:
            return None
        tid = platform_cfg.get("topics", {}).get(topic, {}).get("thread_id")
        return int(tid) if tid else None

    def _format_caption(self, category_data: Dict, hashtags: List[str], platform_id: str) -> str:
        config = load_config()
        emoji_row = category_data.get("emoji_row", "")
        title = f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}"
        rows = [" ".join(hashtags[i:i+3]) for i in range(0, len(hashtags), 3)]
        platform_cfg = config.get("platforms", {}).get(platform_id, {})
        invite_link = platform_cfg.get("invite_link", "https://t.me/your_channel")
        platform_type = platform_cfg.get("type", "group")
        referral_bot = platform_cfg.get("referral_bot", "@Tag_Referral_bot")
        caption = f"{title}\n\n{emoji_row}\n"
        if rows:
            caption += "\n".join(rows) + "\n"
        caption += f"{emoji_row}\n\n"
        if platform_type == "channel":
            caption += f"Получить бесплатный доступ к привату ➡️ {referral_bot}\n"
        caption += f"↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        return caption

    def _format_comic_caption(self, category_data: Dict, comic_title: str, comic_link: str,
                               hashtags: List[str], platform_id: str) -> str:
        config = load_config()
        emoji_row = category_data.get("emoji_row", "")
        title = f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}"
        platform_cfg = config.get("platforms", {}).get(platform_id, {})
        invite_link = platform_cfg.get("invite_link", "https://t.me/your_channel")
        platform_type = platform_cfg.get("type", "group")
        referral_bot = platform_cfg.get("referral_bot", "@Tag_Referral_bot")
        caption = f"{title}\n\n{emoji_row}\n\n"
        caption += f'📖 <b><u><a href="{comic_link}">{comic_title}</a></u></b>\n\n'
        if hashtags:
            rows = [" ".join(hashtags[i:i+3]) for i in range(0, len(hashtags), 3)]
            caption += "\n".join(rows) + "\n\n"
        caption += f"{emoji_row}\n\n"
        if platform_type == "channel":
            caption += f"Получить бесплатный доступ к привату ➡️ {referral_bot}\n"
        caption += f"↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        return caption
