"""
queue_manager.py — менеджер очереди публикаций

✅ ОБНОВЛЕНО:
- Отслеживание активных пакетов (_running_batch_items)
- Параллельная публикация нескольких пакетов одновременно
- Детальный прогресс: опубликовано/осталось для графиков
- get_queue() возвращает активные пакеты + очередь
"""
import asyncio
import json
import logging
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)

KYIV_TZ = pytz.timezone('Europe/Kiev')


def load_config() -> dict:
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


class QueueManager:
    def __init__(self, filepath: str = "queue.json"):
        self.filepath = Path(filepath)
        self._queue: List[Dict] = self._load()
        self.running: bool = False
        self.interval: int = 60
        self._task: Optional[asyncio.Task] = None
        self._active_id: Optional[str] = None
        self._active_progress: str = ""

        # ✅ НОВОЕ: Словарь АКТИВНЫХ (прямо сейчас публикующихся) пакетов
        # {batch_id: item_dict_with_progress}
        self._running_batch_items: Dict[str, Dict] = {}

        self.batch_service = None

    # ─────────────────────── Persistence ───────────────────────

    def _load(self) -> List[Dict]:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in data:
                    if "schedule" in item:
                        item["schedule"] = [
                            datetime.fromisoformat(s) if isinstance(s, str) else s
                            for s in item["schedule"]
                        ]
                return data
            except Exception as e:
                logger.error(f"Ошибка загрузки очереди: {e}")
        return []

    def _save(self):
        try:
            data = []
            for item in self._queue:
                ic = dict(item)
                if "schedule" in ic:
                    ic["schedule"] = [
                        s.isoformat() if isinstance(s, datetime) else s
                        for s in ic["schedule"]
                    ]
                data.append(ic)
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения очереди: {e}")

    # ─────────────────────── Add items ───────────────────────

    def add_to_queue(self, user_id, name, category_key, category_data, platform_id,
                     media_files, selected_hashtags, description="", topic=None,
                     is_comic=False, comic_link="", comic_title="") -> str:
        item_id = str(uuid.uuid4())[:8]
        self._queue.append({
            "id": item_id, "type": "single", "user_id": user_id, "name": name,
            "category_key": category_key, "category_data": category_data,
            "platform_id": platform_id, "media_files": media_files,
            "selected_hashtags": selected_hashtags, "description": description,
            "topic": topic, "is_comic": is_comic, "comic_link": comic_link,
            "comic_title": comic_title, "created_at": datetime.now().isoformat(),
        })
        self._save()
        return item_id

    def add_batch_to_queue(self, user_id, category_data, category_key, platform_id,
                           posts, selected_hashtags, schedule, topic, time_period,
                           time_value, shuffle=False,
                           selected_dates=None, selected_times=None) -> str:
        """
        Добавляет пакет в очередь.
        selected_dates / selected_times — для кастомного графика.
        time_period == 'custom' означает пакет с кастомным графиком.
        """
        batch_id = str(uuid.uuid4())[:8]

        shuffled_posts = list(posts)
        if shuffle:
            random.shuffle(shuffled_posts)

        self._queue.append({
            "id": batch_id,
            "type": "batch",
            "user_id": user_id,
            "name": f"Пакет #{batch_id[:4]}",
            "category_key": category_key,
            "category_data": category_data,
            "platform_id": platform_id,
            "posts": shuffled_posts,
            "selected_hashtags": selected_hashtags,
            "schedule": schedule,
            "topic": topic,
            "time_period": time_period,
            "time_value": time_value,
            "shuffle": shuffle,
            "selected_dates": selected_dates or [],
            "selected_times": selected_times or [],
            "created_at": datetime.now().isoformat(),
        })
        self._save()
        shuffle_status = "перемешан" if shuffle else "по порядку"
        logger.info(f"Пакет #{batch_id[:4]} ({len(posts)} постов, {shuffle_status}) добавлен в очередь")
        return batch_id

    def merge_batch_into_active(self, user_id, category_data, category_key, platform_id,
                                posts, selected_hashtags, topic, time_period, time_value,
                                shuffle=False) -> Optional[str]:
        """Совмещает новый пакет с активным — посты чередуются."""
        if not self._queue:
            return None
        active = self._queue[0]
        if active.get("type") != "batch":
            return None
        active_schedule = active.get("schedule", [])
        if not active_schedule:
            return None

        new_posts = list(posts)
        if shuffle:
            random.shuffle(new_posts)

        now = datetime.now()
        if len(active_schedule) >= 2:
            s0 = active_schedule[0] if isinstance(active_schedule[0], datetime) else datetime.fromisoformat(active_schedule[0])
            s1 = active_schedule[1] if isinstance(active_schedule[1], datetime) else datetime.fromisoformat(active_schedule[1])
            interval_sec = max((s1 - s0).total_seconds(), 60)
        else:
            interval_sec = 3600

        next_t = None
        for s in active_schedule:
            t = s if isinstance(s, datetime) else datetime.fromisoformat(s)
            if t > now:
                next_t = t
                break
        if next_t is None:
            next_t = now + timedelta(seconds=interval_sec)

        half = interval_sec / 2
        new_schedule = [next_t + timedelta(seconds=half + i * interval_sec) for i in range(len(new_posts))]

        return self.add_batch_to_queue(
            user_id=user_id, category_data=category_data, category_key=category_key,
            platform_id=platform_id, posts=new_posts, selected_hashtags=selected_hashtags,
            schedule=new_schedule, topic=topic, time_period=time_period,
            time_value=time_value, shuffle=False,
        )

    # ─────────────────────── Queue access ───────────────────────

    def get_queue(self, user_id: int = None) -> List[Dict]:
        """
        Возвращает сначала АКТИВНЫЕ (публикующиеся) пакеты, затем ожидающие.
        ✅ ИСПРАВЛЕНО: если передан user_id — показывает только ЕГО элементы.
        """
        result = []
        for batch_id, item in self._running_batch_items.items():
            if user_id is None or item.get("user_id") == user_id:
                result.append(dict(item))
        for item in self._queue:
            if user_id is None or item.get("user_id") == user_id:
                result.append(dict(item))
        return result

    def get_pending_queue(self, user_id: int = None) -> List[Dict]:
        """Только ожидающие элементы.
        ✅ ИСПРАВЛЕНО: фильтрация по user_id."""
        if user_id is None:
            return list(self._queue)
        return [item for item in self._queue if item.get("user_id") == user_id]

    def get_pending_queue_indices(self, user_id: int) -> List[int]:
        """Возвращает реальные индексы в self._queue для элементов пользователя."""
        return [i for i, item in enumerate(self._queue) if item.get("user_id") == user_id]

    def get_running_batch_ids(self, user_id: int = None) -> set:
        if user_id is None:
            return set(self._running_batch_items.keys())
        return {bid for bid, item in self._running_batch_items.items() if item.get("user_id") == user_id}

    def get_running_batch(self, batch_id: str) -> Optional[Dict]:
        return self._running_batch_items.get(batch_id)

    def is_batch_active(self) -> bool:
        return bool(self._running_batch_items)

    def is_batch_active_for_category(self, category_key: str = None, topic: str = None) -> bool:
        """Проверяет, идёт ли пакет для конкретной категории/топика."""
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

    # ─────────────────────── Mutate queue ───────────────────────

    def remove_from_queue(self, index: int, user_id: int = None) -> bool:
        """
        ✅ ИСПРАВЛЕНО: index теперь — позиция среди элементов ЭТОГО пользователя.
        Нельзя удалить активно публикующиеся пакеты.
        """
        # Переводим пользовательский индекс в реальный индекс self._queue
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
                batch_id = item.get("id")
                if batch_id:
                    self.batch_service.cancel_batch_tasks(batch_id)
            self._save()
            logger.info(f"Удалён из очереди: {item.get('name')}")
            return True
        return False

    def clear_queue(self, user_id: int = None, skip_active: bool = True):
        """
        ✅ ИСПРАВЛЕНО: очищает только элементы конкретного пользователя.
        Если user_id=None — очищает всю очередь (для системных нужд).
        """
        if user_id is not None:
            # Удаляем только элементы этого юзера
            active_id = self._active_id if (skip_active and self.running) else None
            self._queue = [
                item for item in self._queue
                if item.get("user_id") != user_id or item.get("id") == active_id
            ]
        elif skip_active and self.running and self._queue:
            self._queue = [self._queue[0]]
        else:
            self._queue.clear()
        self._save()
        logger.info(f"🗑️ Очередь очищена (user_id={user_id})")

    def set_interval(self, minutes: int):
        self.interval = max(1, int(minutes))

    # ─────────────────────── Status ───────────────────────

    def get_status(self, user_id: int = None) -> Dict:
        """✅ ИСПРАВЛЕНО: статус и счётчики фильтруются по user_id."""
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
        """✅ ИСПРАВЛЕНО: показывает только данные конкретного пользователя."""
        s = self.get_status(user_id)
        icon = "🟢 Активна" if s["running"] else "🔴 Остановлена"
        text = f"📥 <b>Очередь публикаций</b>\n\nСтатус: <b>{icon}</b>\n"

        running_batches = s.get("running_batches", {})
        if s["running"] and running_batches:
            text += f"\n<b>▶️ Публикуется прямо сейчас ({len(running_batches)}):</b>\n"
            for batch_id, item in running_batches.items():
                cat = item.get("category_data", {})
                cat_name = f"{cat.get('emoji', '')} {cat.get('name', '—')}"
                published = item.get("published_count", 0)
                total = len(item.get("posts", []))
                text += f"• {item.get('name', batch_id)} [{cat_name}]: <b>{published}/{total}</b>\n"
        elif s["running"] and s["active_progress"]:
            text += f"▶️ Публикуется: <i>{s['active_progress']}</i>\n"

        text += f"\n📋 Элементов в очереди: <b>{s['pending']}</b>\n"
        text += f"⏱ Интервал: <b>{s['interval']} мин</b>\n"

        if running_batches:
            text += "\n💡 Нажмите <b>📊 Прогресс</b> для деталей"

        return text

    def get_running_batch_detail_text(self, batch_id: str) -> str:
        """Детальный текст прогресса одного активного пакета."""
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
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = KYIV_TZ.localize(dt)
                    dt_kyiv = dt.astimezone(KYIV_TZ)
                    text += f"  • {dt_kyiv.strftime('%d.%m %H:%M')}\n"
                except Exception:
                    text += f"  • {ts}\n"
            text += "\n"

        remaining_schedule = schedule[published_count:]
        if remaining_schedule:
            text += "⏳ <b>Предстоит:</b>\n"
            for s in remaining_schedule[:10]:
                try:
                    if isinstance(s, str):
                        dt = datetime.fromisoformat(s)
                    else:
                        dt = s
                    if dt.tzinfo is None:
                        dt = KYIV_TZ.localize(dt)
                    dt_kyiv = dt.astimezone(KYIV_TZ)
                    text += f"  • {dt_kyiv.strftime('%d.%m %H:%M')}\n"
                except Exception:
                    text += f"  • {s}\n"
            if len(remaining_schedule) > 10:
                text += f"  ... и ещё {len(remaining_schedule) - 10}\n"

        return text

    def get_all_progress_text(self, user_id: int = None) -> str:
        """✅ ИСПРАВЛЕНО: прогресс только своих активных пакетов."""
        items = {
            bid: item for bid, item in self._running_batch_items.items()
            if user_id is None or item.get("user_id") == user_id
        }
        if not items:
            return "▶️ Нет активно публикующихся пакетов"
        text = ""
        for batch_id in items:
            text += self.get_running_batch_detail_text(batch_id)
            text += "\n" + "─" * 25 + "\n\n"
        return text.rstrip()

    # ─────────────────────── Queue control ───────────────────────

    async def start_queue(self, media_processor, bot) -> bool:
        if not self._queue:
            return False
        if self.running and self._task and not self._task.done():
            return True

        from batch_service import BatchPostingService
        self.batch_service = BatchPostingService(media_processor, bot)

        self.running = True
        self._task = asyncio.create_task(self._process_queue_loop(media_processor, bot))
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

    # ─────────────────────── Queue loop ───────────────────────

    async def _process_queue_loop(self, media_processor, bot):
        """
        ✅ ИСПРАВЛЕНО: Цикл НЕ завершается пока есть активные batch-задачи.

        БАГИ ДО ИСПРАВЛЕНИЯ:
        - while self.running and self._queue → при пустой очереди цикл выходил
          даже если batch-задачи ещё выполнялись и running=True
        - Если юзер добавлял второй пакет после того как первый стартовал
          и очередь стала пустой — новый пакет зависал навсегда

        РЕШЕНИЕ:
        - Цикл крутится пока self.running == True
        - Если queue пуста но есть активные задачи — ждём 2с и проверяем снова
        - Если queue пуста И задач нет — выходим
        """
        logger.info("▶️ Цикл очереди запущен")
        batch_tasks: list = []  # list of (batch_id, asyncio.Task)

        while self.running:
            # Убираем завершённые задачи из списка
            batch_tasks = [(bid, t) for bid, t in batch_tasks if not t.done()]

            # Очередь пуста — проверяем есть ли активные задачи
            if not self._queue:
                if not batch_tasks:
                    break  # Всё готово — выходим
                # Есть активные пакеты — ждём, может придут новые элементы
                await asyncio.sleep(2)
                continue

            item = self._queue[0]
            self._active_id = item["id"]

            try:
                if item["type"] == "batch":
                    logger.info(f"🚀 Запуск пакета {item.get('name', 'Пакет')} параллельно")

                    # Сохраняем в _running_batch_items ДО удаления из очереди
                    running_item = {
                        **{k: v for k, v in item.items() if k != "schedule"},
                        "schedule": list(item.get("schedule", [])),
                        "_is_running": True,
                        "published_count": 0,
                        "published_log": [],
                    }
                    self._running_batch_items[item["id"]] = running_item

                    task = asyncio.create_task(
                        self._publish_batch(item["id"], item, media_processor, bot)
                    )
                    batch_tasks.append((item["id"], task))

                    # Удаляем из ожидающей очереди — НЕ блокируем цикл
                    self._queue.pop(0)
                    self._save()

                    self._active_id = None
                    self._active_progress = ""
                    # Сразу берём следующий элемент без sleep
                    continue

                else:
                    # Обычный пост — последовательно
                    await self._publish_single(item, media_processor, bot)
                    if self._queue and self._queue[0].get("id") == item["id"]:
                        self._queue.pop(0)
                        self._save()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка публикации: {e}")
                if self._queue and self._queue[0].get("id") == item.get("id"):
                    self._queue.pop(0)
                    self._save()

            self._active_id = None
            self._active_progress = ""

            # Интервал только для обычных постов (не batch)
            if self._queue and self.running:
                await asyncio.sleep(self.interval * 60)

        # Финальное ожидание незавершённых задач
        batch_tasks = [(bid, t) for bid, t in batch_tasks if not t.done()]
        if batch_tasks:
            logger.info(f"⏳ Ожидание завершения {len(batch_tasks)} активных пакетов...")
            for batch_id, task in batch_tasks:
                try:
                    await task
                except Exception as e:
                    logger.error(f"Ошибка пакета {batch_id}: {e}")

        self._active_id = None
        self._active_progress = ""
        self.running = False
        logger.info("✅ Очередь завершена")

    # ─────────────────────── Publish helpers ───────────────────────

    async def _publish_single(self, item: Dict, media_processor, bot):
        self._active_progress = item.get("name", "Пост")
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
                caption += f"Получить бесплатно доступ к группе ➡️ {referral_bot}\n"
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
            caption = self._format_caption(category_data, item.get("selected_hashtags", []), platform_id)
            if len(processed) == 1:
                await media_processor.send_single_media(chat_id, processed[0][0], processed[0][1], caption, thread_id)
            else:
                await media_processor.send_media_group(chat_id, processed, caption, thread_id)
            media_processor.cleanup_specific([p for _, p in processed])
        logger.info(f"✅ '{item.get('name')}' опубликован")

    async def _publish_batch(self, batch_id: str, item: Dict, media_processor, bot):
        """
        ✅ Публикует пакет по расписанию, обновляя прогресс в _running_batch_items.
        """
        posts = item.get("posts", [])
        schedule = item.get("schedule", [])
        category_data = item["category_data"]
        platform_id = item["platform_id"]
        topic = item.get("topic")
        batch_name = item.get("name", "Пакет")
        total = len(posts)

        config = load_config()
        platform_cfg = config.get("platforms", {}).get(platform_id, {})
        chat_id = int(platform_cfg.get("chat_id", 0))
        thread_id = self._resolve_thread(platform_cfg, topic)
        caption = self._format_caption(category_data, item.get("selected_hashtags", []), platform_id)

        for i, (post_media_ids, sched_time) in enumerate(zip(posts, schedule)):
            if not self.running:
                break

            if batch_id in self._running_batch_items:
                self._running_batch_items[batch_id]["published_count"] = i

            if isinstance(sched_time, str):
                sched_time = datetime.fromisoformat(sched_time)

            if hasattr(sched_time, 'tzinfo') and sched_time.tzinfo is not None:
                now_aware = datetime.now(sched_time.tzinfo)
                wait_sec = (sched_time - now_aware).total_seconds()
            else:
                wait_sec = (sched_time - datetime.now()).total_seconds()

            if wait_sec > 0:
                logger.info(f"⏳ {batch_name} пост {i+1}/{total} через {wait_sec:.0f}с")
                while wait_sec > 0 and self.running:
                    await asyncio.sleep(min(wait_sec, 5.0))
                    if hasattr(sched_time, 'tzinfo') and sched_time.tzinfo is not None:
                        wait_sec = (sched_time - datetime.now(sched_time.tzinfo)).total_seconds()
                    else:
                        wait_sec = (sched_time - datetime.now()).total_seconds()
            else:
                # ✅ ИСПРАВЛЕНО: Время уже прошло (бот перезапускался на Render).
                # Публикуем сразу с паузой 3с между постами чтобы не спамить канал.
                missed_mins = int(abs(wait_sec) / 60)
                logger.info(
                    f"⚡ {batch_name} пост {i+1}/{total}: опоздание {missed_mins} мин "
                    f"(перезапуск Render) — публикуем сразу"
                )
                if i > 0:
                    await asyncio.sleep(3)

            if not self.running:
                break

            try:
                if post_media_ids and isinstance(post_media_ids[0], (list, tuple)):
                    media_to_process = [(m[0], m[1]) for m in post_media_ids]
                else:
                    media_to_process = [(post_media_ids[0], post_media_ids[1])]

                processed = await media_processor.process_batch(media_to_process)
                if not processed:
                    continue
                if len(processed) == 1:
                    await media_processor.send_single_media(chat_id, processed[0][0], processed[0][1], caption, thread_id)
                else:
                    await media_processor.send_media_group(chat_id, processed, caption, thread_id)
                media_processor.cleanup_specific([p for _, p in processed])

                # ✅ Логируем успешную публикацию
                published_at = datetime.now(KYIV_TZ).isoformat()
                if batch_id in self._running_batch_items:
                    self._running_batch_items[batch_id]["published_count"] = i + 1
                    self._running_batch_items[batch_id]["published_log"].append(published_at)

                logger.info(f"✅ {batch_name}: {i+1}/{total}")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ошибка поста {i+1}: {e}")

        # ✅ Убираем из активных
        if batch_id in self._running_batch_items:
            del self._running_batch_items[batch_id]

        logger.info(f"✅ Пакет {batch_name} завершён")

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
            caption += f"Получить бесплатно доступ к группе ➡️ {referral_bot}\n"
        caption += f"↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        return caption
