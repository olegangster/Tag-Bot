"""
queue_manager.py — менеджер очереди публикаций

КЛЮЧЕВЫЕ ФИЧИ:
- Параллельная публикация по разным категориям (Альтушки и 3Д публикуются одновременно)
- Последовательная публикация внутри одной категории
- Вопрос о чередовании ТОЛЬКО при добавлении в ту же категорию
"""
import asyncio
import json
import logging
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def load_config() -> dict:
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


class QueueManager:
    def __init__(self, filepath: str = "queue.json"):
        self.filepath = Path(filepath)
        self._items: List[Dict] = self._load()   # все элементы в порядке добавления
        self._tasks: Dict[str, asyncio.Task] = {}  # category_key -> Task
        self._active: Dict[str, str] = {}           # category_key -> id активного элемента
        self._progress: Dict[str, str] = {}         # category_key -> строка прогресса
        self.running: bool = False
        self.interval: int = 60

    # ─────────────────────── persistence ───────────────────────

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
            for item in self._items:
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

    # ─────────────────────── category helpers ───────────────────────

    def _cat_key(self, item: Dict) -> str:
        """Ключ категории для группировки параллельных задач"""
        return item.get("category_key", "default")

    def is_category_active(self, category_key: str) -> bool:
        """Есть ли активная задача публикации для данной категории?"""
        task = self._tasks.get(category_key)
        return task is not None and not task.done()

    def get_active_info_for_category(self, category_key: str) -> Optional[Dict]:
        """Активный элемент для категории"""
        active_id = self._active.get(category_key)
        if not active_id:
            return None
        for item in self._items:
            if item.get("id") == active_id:
                return item
        return None

    def get_progress_for_category(self, category_key: str) -> str:
        return self._progress.get(category_key, "")

    # ─────────────────────── add items ───────────────────────

    def add_to_queue(self, user_id, name, category_key, category_data, platform_id,
                     media_files, selected_hashtags, description="", topic=None,
                     is_comic=False, comic_link="", comic_title="") -> str:
        item_id = str(uuid.uuid4())[:8]
        self._items.append({
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
                           time_value, shuffle=False) -> str:
        batch_id = str(uuid.uuid4())[:8]
        # shuffle уже применён до вызова этой функции если нужен
        final_posts = list(posts)
        if shuffle:
            random.shuffle(final_posts)
        self._items.append({
            "id": batch_id, "type": "batch", "user_id": user_id,
            "name": f"Пакет #{batch_id[:4]}", "category_key": category_key,
            "category_data": category_data, "platform_id": platform_id,
            "posts": final_posts, "selected_hashtags": selected_hashtags,
            "schedule": schedule, "topic": topic, "time_period": time_period,
            "time_value": time_value, "shuffle": shuffle,
            "created_at": datetime.now().isoformat(),
        })
        self._save()
        logger.info(f"Пакет #{batch_id[:4]} ({len(posts)} постов) добавлен [{category_key}]")
        return batch_id

    def merge_batch_into_active(self, user_id, category_data, category_key, platform_id,
                                posts, selected_hashtags, topic, time_period, time_value,
                                shuffle=False) -> Optional[str]:
        """Совмещает новый пакет с активным ТОЙ ЖЕ категории — посты чередуются"""
        active = self.get_active_info_for_category(category_key)
        if not active or active.get("type") != "batch":
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

    # ─────────────────────── queue control ───────────────────────

    def get_queue(self) -> List[Dict]:
        return list(self._items)

    def remove_from_queue(self, index: int) -> bool:
        """Удаляет элемент по индексу. Нельзя удалить активно публикуемый."""
        if index < 0 or index >= len(self._items):
            return False
        item = self._items[index]
        cat_key = self._cat_key(item)
        if self._active.get(cat_key) == item.get("id"):
            return False  # сейчас публикуется
        self._items.pop(index)
        self._save()
        logger.info(f"Удалён из очереди: {item.get('name')}")
        return True

    def clear_queue(self, skip_active: bool = True):
        if skip_active:
            active_ids = set(self._active.values())
            self._items = [i for i in self._items if i.get("id") in active_ids]
        else:
            self._items.clear()
        self._save()

    def set_interval(self, minutes: int):
        self.interval = max(1, int(minutes))

    # ─────────────────────── status ───────────────────────

    @property
    def _active_progress(self) -> str:
        """Для обратной совместимости"""
        return " | ".join(v for v in self._progress.values() if v)

    def get_status(self) -> Dict:
        return {
            "running": self.running,
            "pending": len(self._items),
            "interval": self.interval,
            "active_progress": self._active_progress,
        }

    def get_status_text(self) -> str:
        s = self.get_status()
        icon = "🟢 Активна" if s["running"] else "🔴 Остановлена"
        text = f"📥 <b>Очередь публикаций</b>\n\nСтатус: <b>{icon}</b>\n"

        if s["running"]:
            for cat_key, progress in self._progress.items():
                if progress:
                    text += f"▶️ <i>{progress}</i>\n"

        text += f"📋 Элементов в очереди: <b>{s['pending']}</b>\n"
        text += f"⏱ Интервал: <b>{s['interval']} мин</b>\n"

        active_count = sum(1 for t in self._tasks.values() if not t.done())
        if active_count > 1:
            text += f"🔀 Параллельных категорий: <b>{active_count}</b>\n"

        return text

    def is_batch_active(self) -> bool:
        """Обратная совместимость"""
        for cat_key, active_id in self._active.items():
            for item in self._items:
                if item.get("id") == active_id and item.get("type") == "batch":
                    return True
        return False

    def get_active_info(self) -> Optional[Dict]:
        """Обратная совместимость — первый попавшийся активный"""
        for cat_key, active_id in self._active.items():
            for item in self._items:
                if item.get("id") == active_id:
                    return item
        return None

    # ─────────────────────── start / stop ───────────────────────

    async def start_queue(self, media_processor, bot) -> bool:
        """Запускает задачи для всех категорий в очереди параллельно"""
        if not self._items:
            return False

        self.running = True
        categories = set(self._cat_key(item) for item in self._items)
        for cat_key in categories:
            if not self.is_category_active(cat_key):
                self._tasks[cat_key] = asyncio.create_task(
                    self._process_category_loop(cat_key, media_processor, bot)
                )
                logger.info(f"▶️ Запущена задача для [{cat_key}]")

        return True

    async def ensure_category_running(self, cat_key: str, media_processor, bot):
        """
        Запускает задачу для конкретной категории если она ещё не запущена.
        Вызывается после добавления нового элемента в очередь.
        """
        if not self.is_category_active(cat_key):
            self.running = True
            self._tasks[cat_key] = asyncio.create_task(
                self._process_category_loop(cat_key, media_processor, bot)
            )
            logger.info(f"▶️ Автозапуск [{cat_key}]")

    async def stop_queue(self):
        self.running = False
        for cat_key, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks.clear()
        self._active.clear()
        self._progress.clear()
        logger.info("⏹ Очередь остановлена")

    # ─────────────────────── processing loops ───────────────────────

    async def _process_category_loop(self, cat_key: str, media_processor, bot):
        """
        Обрабатывает очередь ОДНОЙ категории независимо от других категорий.
        Альтушки и 3Д работают параллельно и не ждут друг друга.
        """
        logger.info(f"▶️ Цикл [{cat_key}] запущен")
        first_item = True

        while self.running:
            item = self._get_next_pending(cat_key)
            if item is None:
                break

            self._active[cat_key] = item["id"]

            try:
                # Интервал между элементами внутри одной категории
                if not first_item:
                    logger.info(f"⏳ [{cat_key}] ждём {self.interval} мин до следующего элемента")
                    await asyncio.sleep(self.interval * 60)

                if item["type"] == "batch":
                    await self._publish_batch(item, media_processor, bot, cat_key)
                else:
                    await self._publish_single(item, media_processor, bot, cat_key)

                # Удаляем опубликованный элемент
                self._items = [i for i in self._items if i.get("id") != item["id"]]
                self._save()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка публикации [{cat_key}]: {e}")
                self._items = [i for i in self._items if i.get("id") != item["id"]]
                self._save()

            first_item = False
            self._active.pop(cat_key, None)
            self._progress.pop(cat_key, None)

        self._active.pop(cat_key, None)
        self._progress.pop(cat_key, None)
        self._tasks.pop(cat_key, None)

        if not any(not t.done() for t in self._tasks.values()):
            self.running = False

        logger.info(f"✅ Цикл [{cat_key}] завершён")

    def _get_next_pending(self, cat_key: str) -> Optional[Dict]:
        """Следующий элемент для категории (не активный)"""
        active_id = self._active.get(cat_key)
        for item in self._items:
            if self._cat_key(item) == cat_key and item.get("id") != active_id:
                return item
        return None

    async def _publish_single(self, item: Dict, media_processor, bot, cat_key: str):
        self._progress[cat_key] = item.get("name", "Пост")
        category_data = item["category_data"]
        platform_id = item["platform_id"]
        topic = item.get("topic")

        config = load_config()
        platform_cfg = config.get("platforms", {}).get(platform_id, {})
        chat_id = int(platform_cfg.get("chat_id", 0))
        thread_id = self._resolve_thread(platform_cfg, topic)

        if item.get("is_comic"):
            invite_link = platform_cfg.get("invite_link", "https://t.me/your_channel")
            caption = (
                f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}\n\n"
                f"{category_data.get('emoji_row', '')}\n\n"
                f'📖 <b><u><a href="{item.get("comic_link","")}">{ item.get("comic_title","")}</a></u></b>\n\n'
                f"{category_data.get('emoji_row', '')}\n\n"
                f"↘️⬇️↙️\n"
                f'<a href="{invite_link}">Ссылка для друга</a>'
            )
            kwargs = dict(chat_id=chat_id, text=caption, parse_mode="HTML")
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_message(**kwargs)
        else:
            media_files = item.get("media_files", [])
            media_to_process = [(mtype, fid) for mtype, fid, *_ in media_files]
            processed = await media_processor.process_batch(media_to_process)
            if not processed:
                return
            caption = self._format_caption(category_data, item.get("selected_hashtags", []), platform_id)
            if len(processed) == 1:
                await media_processor.send_single_media(chat_id, processed[0][0], processed[0][1], caption, thread_id)
            else:
                await media_processor.send_media_group(chat_id, processed, caption, thread_id)
            media_processor.cleanup_specific([p for _, p in processed])
        logger.info(f"✅ '{item.get('name')}' опубликован [{cat_key}]")

    async def _publish_batch(self, item: Dict, media_processor, bot, cat_key: str):
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
            self._progress[cat_key] = f"{batch_name}: {i+1}/{total}"

            if isinstance(sched_time, str):
                sched_time = datetime.fromisoformat(sched_time)
            wait_sec = (sched_time - datetime.now()).total_seconds()
            if wait_sec > 0:
                logger.info(f"⏳ [{cat_key}] {batch_name} пост {i+1}/{total} через {wait_sec:.0f}с")
                while wait_sec > 0 and self.running:
                    await asyncio.sleep(min(wait_sec, 5.0))
                    wait_sec = (sched_time - datetime.now()).total_seconds()

            if not self.running:
                break

            try:
                if post_media_ids and isinstance(post_media_ids[0], (list, tuple)):
                    media_to_process = [(mtype, fid) for mtype, fid, *_ in post_media_ids]
                else:
                    media_to_process = list(post_media_ids)

                processed = await media_processor.process_batch(media_to_process)
                if not processed:
                    continue
                if len(processed) == 1:
                    await media_processor.send_single_media(chat_id, processed[0][0], processed[0][1], caption, thread_id)
                else:
                    await media_processor.send_media_group(chat_id, processed, caption, thread_id)
                media_processor.cleanup_specific([p for _, p in processed])
                logger.info(f"✅ [{cat_key}] {batch_name}: {i+1}/{total}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ошибка поста {i+1} [{cat_key}]: {e}")

    def _resolve_thread(self, platform_cfg: Dict, topic: Optional[str]) -> Optional[int]:
        if not topic:
            return None
        tid = platform_cfg.get("topics", {}).get(topic, {}).get("thread_id")
        return int(tid) if tid else None

    def _format_caption(self, category_data: Dict, hashtags: List[str], platform_id: str) -> str:
        config = load_config()
        emoji_row = category_data.get("emoji_row", "")
        title = f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}"
        rows = [" ".join(hashtags[i:i+4]) for i in range(0, len(hashtags), 4)]
        invite_link = config.get("platforms", {}).get(platform_id, {}).get("invite_link", "https://t.me/your_channel")
        caption = f"{title}\n\n{emoji_row}\n"
        if rows:
            caption += "\n".join(rows) + "\n"
        caption += f"{emoji_row}\n\n↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        return caption
