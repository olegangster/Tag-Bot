"""
queue_manager.py — менеджер очереди публикаций
"""
import asyncio
import json
import logging
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

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
                           time_value, shuffle=False) -> str:
        batch_id = str(uuid.uuid4())[:8]
        shuffled_posts = list(posts)
        if shuffle:
            random.shuffle(shuffled_posts)
        self._queue.append({
            "id": batch_id, "type": "batch", "user_id": user_id,
            "name": f"Пакет #{batch_id[:4]}", "category_key": category_key,
            "category_data": category_data, "platform_id": platform_id,
            "posts": shuffled_posts, "selected_hashtags": selected_hashtags,
            "schedule": schedule, "topic": topic, "time_period": time_period,
            "time_value": time_value, "shuffle": shuffle,
            "created_at": datetime.now().isoformat(),
        })
        self._save()
        logger.info(f"Пакет #{batch_id[:4]} ({len(posts)} постов) добавлен в очередь")
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

        # Вычисляем интервал активного пакета
        now = datetime.now()
        if len(active_schedule) >= 2:
            s0 = active_schedule[0] if isinstance(active_schedule[0], datetime) else datetime.fromisoformat(active_schedule[0])
            s1 = active_schedule[1] if isinstance(active_schedule[1], datetime) else datetime.fromisoformat(active_schedule[1])
            interval_sec = max((s1 - s0).total_seconds(), 60)
        else:
            interval_sec = 3600

        # Следующее время из активного расписания (в будущем)
        next_t = None
        for s in active_schedule:
            t = s if isinstance(s, datetime) else datetime.fromisoformat(s)
            if t > now:
                next_t = t
                break
        if next_t is None:
            next_t = now + timedelta(seconds=interval_sec)

        # Новые посты сдвинуты на половину интервала относительно активных
        half = interval_sec / 2
        new_schedule = [next_t + timedelta(seconds=half + i * interval_sec) for i in range(len(new_posts))]

        return self.add_batch_to_queue(
            user_id=user_id, category_data=category_data, category_key=category_key,
            platform_id=platform_id, posts=new_posts, selected_hashtags=selected_hashtags,
            schedule=new_schedule, topic=topic, time_period=time_period,
            time_value=time_value, shuffle=False,
        )

    def get_queue(self) -> List[Dict]:
        return list(self._queue)

    def is_batch_active(self) -> bool:
        return bool(self._active_id and self._queue
                    and self._queue[0].get("id") == self._active_id
                    and self._queue[0].get("type") == "batch")

    def remove_from_queue(self, index: int) -> bool:
        """Удаляет элемент. Нельзя удалить активно публикуемый (index 0 пока running)."""
        if self.running and index == 0:
            return False
        if 0 <= index < len(self._queue):
            item = self._queue.pop(index)
            self._save()
            logger.info(f"Удалён из очереди: {item.get('name')}")
            return True
        return False

    def clear_queue(self, skip_active: bool = True):
        if skip_active and self.running and self._queue:
            self._queue = [self._queue[0]]
        else:
            self._queue.clear()
        self._save()

    def set_interval(self, minutes: int):
        self.interval = max(1, int(minutes))

    def get_status(self) -> Dict:
        return {
            "running": self.running,
            "pending": len(self._queue),
            "interval": self.interval,
            "active_progress": self._active_progress,
        }

    def get_status_text(self) -> str:
        s = self.get_status()
        icon = "🟢 Активна" if s["running"] else "🔴 Остановлена"
        text = f"📥 <b>Очередь публикаций</b>\n\nСтатус: <b>{icon}</b>\n"
        if s["running"] and s["active_progress"]:
            text += f"▶️ Публикуется: <i>{s['active_progress']}</i>\n"
        total = s["pending"]
        text += f"📋 Элементов в очереди: <b>{total}</b>\n"
        text += f"⏱ Интервал: <b>{s['interval']} мин</b>\n"
        return text

    async def start_queue(self, media_processor, bot) -> bool:
        if not self._queue:
            return False
        if self.running and self._task and not self._task.done():
            return True
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

    async def _process_queue_loop(self, media_processor, bot):
        logger.info("▶️ Цикл очереди запущен")
        while self.running and self._queue:
            item = self._queue[0]
            self._active_id = item["id"]
            try:
                if item["type"] == "batch":
                    await self._publish_batch(item, media_processor, bot)
                else:
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
            if self._queue and self.running:
                await asyncio.sleep(self.interval * 60)
        self._active_id = None
        self._active_progress = ""
        self.running = False
        logger.info("✅ Очередь завершена")

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
        logger.info(f"✅ '{item.get('name')}' опубликован")

    async def _publish_batch(self, item: Dict, media_processor, bot):
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
            self._active_progress = f"{batch_name}: {i+1}/{total}"

            if isinstance(sched_time, str):
                sched_time = datetime.fromisoformat(sched_time)
            wait_sec = (sched_time - datetime.now()).total_seconds()
            if wait_sec > 0:
                logger.info(f"⏳ {batch_name} пост {i+1}/{total} через {wait_sec:.0f}с")
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
                logger.info(f"✅ {batch_name}: {i+1}/{total}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ошибка поста {i+1}: {e}")

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
