import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


class QueueManager:
    """
    Менеджер очереди постов.

    Функции:
    - Добавление постов в очередь
    - Просмотр очереди
    - Запуск/остановка публикации
    - Статус публикации (сколько опубликовано/осталось)
    - Удаление из очереди
    - Настройка интервала
    """

    def __init__(self, filepath: str = "queue.json"):
        self.filepath = Path(filepath)
        self.queue = self._load()
        self.running = False
        self.current_task = None
        self.interval_minutes = 10
        self.published_count = 0
        self.batch_info: Dict = {}

    # ──────────────────────────────────────────────────────
    #  Персистентность
    # ──────────────────────────────────────────────────────

    def _load(self) -> List:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки очереди: {e}")
        return []

    def _save(self):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.queue, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения очереди: {e}")

    # ──────────────────────────────────────────────────────
    #  Добавление в очередь
    # ──────────────────────────────────────────────────────

    def add_to_queue(
        self,
        user_id: int,
        name: str,
        category_key: str,
        category_data: Dict,
        platform_id: str,
        media_files: List[tuple],
        selected_hashtags: List[str],
        description: str = "",
        topic: str = None,
        is_comic: bool = False,
        comic_link: str = "",
        comic_title: str = "",
        scheduled_time: datetime = None,
        batch_id: str = None,
    ) -> str:
        item_id = str(uuid.uuid4())[:8]
        item = {
            "id": item_id,
            "user_id": user_id,
            "name": name,
            "category_key": category_key,
            "category_data": category_data,
            "category_name": category_data.get("name", ""),
            "platform_id": platform_id,
            "topic": topic,
            "media_files": media_files,
            "selected_hashtags": selected_hashtags,
            "description": description,
            "is_comic": is_comic,
            "comic_link": comic_link,
            "comic_title": comic_title,
            "added_at": datetime.now().isoformat(),
            "scheduled_time": scheduled_time.isoformat() if scheduled_time else None,
            "batch_id": batch_id,
            "status": "pending",
        }
        self.queue.append(item)
        self._save()
        logger.info(f"Добавлен в очередь: {item_id} ({name})")
        return item_id

    def add_batch_to_queue(
        self,
        user_id: int,
        category_data: Dict,
        category_key: str,
        platform_id: str,
        posts: List[List[tuple]],
        selected_hashtags: List[str],
        schedule: List[datetime],
        topic: str = None,
        time_period: str = "minute",
        time_value: int = 60,
    ) -> str:
        batch_id = str(uuid.uuid4())[:8]
        self.batch_info[batch_id] = {
            "id": batch_id,
            "total_posts": len(posts),
            "published": 0,
            "failed": 0,
            "start_time": schedule[0].isoformat() if schedule else None,
            "end_time": schedule[-1].isoformat() if len(schedule) > 1 else None,
            "time_period": time_period,
            "time_value": time_value,
            "category_name": category_data.get("name", ""),
            "created_at": datetime.now().isoformat(),
        }
        for i, (post_media, scheduled_time) in enumerate(zip(posts, schedule)):
            self.add_to_queue(
                user_id=user_id,
                name=f"Пакет #{batch_id[:4]} - пост {i + 1}/{len(posts)}",
                category_key=category_key,
                category_data=category_data,
                platform_id=platform_id,
                media_files=post_media,
                selected_hashtags=selected_hashtags,
                topic=topic,
                scheduled_time=scheduled_time,
                batch_id=batch_id,
            )
        logger.info(f"Пакет {batch_id} добавлен в очередь: {len(posts)} постов")
        return batch_id

    # ──────────────────────────────────────────────────────
    #  Геттеры
    # ──────────────────────────────────────────────────────

    def get_queue(self) -> List[Dict]:
        return self.queue

    def get_pending_count(self) -> int:
        return sum(1 for item in self.queue if item.get("status") == "pending")

    def get_published_count(self) -> int:
        return sum(1 for item in self.queue if item.get("status") == "published")

    def get_status(self) -> Dict:
        return {
            "total": len(self.queue),
            "pending": self.get_pending_count(),
            "published": self.get_published_count(),
            "running": self.running,
            "interval": self.interval_minutes,
        }

    def get_status_text(self) -> str:
        status = self.get_status()

        batch_posts: Dict = {}
        for item in self.queue:
            if item.get("batch_id"):
                bid = item["batch_id"]
                if bid not in batch_posts:
                    batch_posts[bid] = {
                        "total": 0,
                        "published": 0,
                        "pending": 0,
                        "failed": 0,
                        "category": item.get("category_name", ""),
                        "scheduled_times": [],
                    }
                batch_posts[bid]["total"] += 1
                s = item.get("status")
                if s == "published":
                    batch_posts[bid]["published"] += 1
                elif s == "pending":
                    batch_posts[bid]["pending"] += 1
                    if item.get("scheduled_time"):
                        batch_posts[bid]["scheduled_times"].append(
                            datetime.fromisoformat(item["scheduled_time"])
                        )
                elif s == "failed":
                    batch_posts[bid]["failed"] += 1

        text = "📥 <b>Статус очереди</b>\n\n"
        text += f"📊 Всего в очереди: {status['total']}\n"
        text += f"⏳ Ожидают: {status['pending']}\n"
        text += f"✅ Опубликовано: {status['published']}\n\n"

        if batch_posts:
            text += "📦 <b>Пакетные отправки:</b>\n\n"
            for bid, info in batch_posts.items():
                text += f"🔹 Пакет #{bid[:4]}\n"
                text += f"   Категория: {info['category']}\n"
                text += f"   Всего: {info['total']} | Опубл: {info['published']} | Ожидают: {info['pending']}\n"
                if info["scheduled_times"]:
                    next_t = min(info["scheduled_times"])
                    last_t = max(info["scheduled_times"])
                    text += f"   ⏰ След. пост: {next_t.strftime('%d.%m %H:%M')}\n"
                    if info["pending"] > 1:
                        text += f"   🕐 Последний: {last_t.strftime('%d.%m %H:%M')}\n"
                if info["failed"] > 0:
                    text += f"   ❌ Ошибок: {info['failed']}\n"
                text += "\n"

        text += f"⏱ Интервал: {status['interval']} минут\n"
        text += f"▶️ Статус: {'Работает' if status['running'] else 'Остановлена'}"
        return text

    def get_batch_status(self, batch_id: str) -> Optional[Dict]:
        batch_posts = [item for item in self.queue if item.get("batch_id") == batch_id]
        if not batch_posts:
            return None
        total = len(batch_posts)
        published = sum(1 for p in batch_posts if p.get("status") == "published")
        pending = sum(1 for p in batch_posts if p.get("status") == "pending")
        failed = sum(1 for p in batch_posts if p.get("status") == "failed")
        scheduled_times = [
            datetime.fromisoformat(p["scheduled_time"])
            for p in batch_posts
            if p.get("scheduled_time") and p.get("status") == "pending"
        ]
        return {
            "id": batch_id,
            "total": total,
            "published": published,
            "pending": pending,
            "failed": failed,
            "next_post_time": min(scheduled_times) if scheduled_times else None,
            "last_post_time": max(scheduled_times) if scheduled_times else None,
            "category_name": batch_posts[0].get("category_name", ""),
        }

    # ──────────────────────────────────────────────────────
    #  Управление очередью
    # ──────────────────────────────────────────────────────

    def remove_from_queue(self, index: int) -> bool:
        try:
            if 0 <= index < len(self.queue):
                removed = self.queue.pop(index)
                self._save()
                logger.info(f"Удален из очереди: {removed.get('name')}")
                return True
        except Exception as e:
            logger.error(f"Ошибка удаления из очереди: {e}")
        return False

    def clear_queue(self):
        self.queue = []
        self._save()
        self.published_count = 0
        logger.info("Очередь очищена")

    def set_interval(self, minutes: int):
        self.interval_minutes = max(1, min(minutes, 1440))
        logger.info(f"Установлен интервал: {self.interval_minutes} минут")

    # ──────────────────────────────────────────────────────
    #  Запуск / остановка
    # ──────────────────────────────────────────────────────

    async def start_queue(self, media_processor, bot) -> bool:
        if self.running:
            logger.warning("Очередь уже запущена")
            return False
        if not self.get_pending_count():
            logger.warning("Нет постов для публикации")
            return False
        self.running = True
        self.published_count = 0
        logger.info(
            f"Запуск очереди: {self.get_pending_count()} постов "
            f"с интервалом {self.interval_minutes} мин"
        )
        self.current_task = asyncio.create_task(
            self._process_queue(media_processor, bot)
        )
        return True

    async def stop_queue(self) -> bool:
        if not self.running:
            return False
        self.running = False
        if self.current_task:
            self.current_task.cancel()
            try:
                await self.current_task
            except asyncio.CancelledError:
                pass
        logger.info("Очередь остановлена")
        return True

    # ──────────────────────────────────────────────────────
    #  Внутренняя обработка
    # ──────────────────────────────────────────────────────

    async def _process_queue(self, media_processor, bot):
        try:
            config_path = Path("config.json")
            if not config_path.exists():
                logger.error("config.json не найден")
                return
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            # Scheduled посты запускаем параллельно
            scheduled_tasks = []
            for item in self.queue:
                if not self.running:
                    break
                if item.get("status") != "pending":
                    continue
                if item.get("scheduled_time"):
                    task = asyncio.create_task(
                        self._publish_scheduled_post(item, media_processor, bot, config)
                    )
                    scheduled_tasks.append(task)

            if scheduled_tasks:
                logger.info(f"Запущено {len(scheduled_tasks)} scheduled постов")
                await asyncio.gather(*scheduled_tasks, return_exceptions=True)

            # Обычные посты (без расписания) — последовательно с интервалом
            for item in self.queue:
                if not self.running:
                    break
                if item.get("status") != "pending":
                    continue
                if item.get("scheduled_time"):
                    continue

                item["status"] = "publishing"
                self._save()
                try:
                    await self._publish_post(item, media_processor, bot, config)
                    item["status"] = "published"
                    item["published_at"] = datetime.now().isoformat()
                    self.published_count += 1
                    self._save()
                    logger.info(
                        f"✅ Опубликован из очереди: {item['name']} ({self.published_count})"
                    )
                    if self.running:
                        await asyncio.sleep(self.interval_minutes * 60)
                except Exception as e:
                    item["status"] = "failed"
                    item["error"] = str(e)
                    self._save()
                    logger.error(f"Ошибка публикации из очереди: {e}")

        except asyncio.CancelledError:
            logger.info("Обработка очереди отменена")
        except Exception as e:
            logger.error(f"Критическая ошибка в _process_queue: {e}")
        finally:
            self.running = False

    async def _publish_scheduled_post(self, item, media_processor, bot, config):
        try:
            scheduled_time = datetime.fromisoformat(item["scheduled_time"])
            wait_seconds = (scheduled_time - datetime.now()).total_seconds()
            if wait_seconds > 0:
                logger.info(
                    f"Пост {item['name']} ожидает {wait_seconds:.0f}с "
                    f"до {scheduled_time.strftime('%H:%M:%S')}"
                )
                await asyncio.sleep(wait_seconds)

            if not self.running:
                return

            item["status"] = "publishing"
            self._save()

            await self._publish_post(item, media_processor, bot, config)

            item["status"] = "published"
            item["published_at"] = datetime.now().isoformat()
            if item.get("batch_id") and item["batch_id"] in self.batch_info:
                self.batch_info[item["batch_id"]]["published"] += 1
            self.published_count += 1
            self._save()
            logger.info(
                f"✅ Scheduled пост опубликован: {item['name']} "
                f"в {datetime.now().strftime('%H:%M:%S')}"
            )
        except asyncio.CancelledError:
            logger.warning(f"Публикация поста {item['name']} отменена")
        except Exception as e:
            item["status"] = "failed"
            item["error"] = str(e)
            if item.get("batch_id") and item["batch_id"] in self.batch_info:
                self.batch_info[item["batch_id"]]["failed"] += 1
            self._save()
            logger.error(f"Ошибка публикации scheduled поста: {e}")

    async def _publish_post(self, item, media_processor, bot, config):
        platform_id = item["platform_id"]
        chat_id = config["platforms"][platform_id]["chat_id"]
        thread_id = None

        if item.get("topic"):
            thread_id = (
                config["platforms"][platform_id]["topics"]
                .get(item["topic"], {})
                .get("thread_id")
            )

        # Комикс — только текстовое сообщение со ссылкой
        if item.get("is_comic", False):
            caption = self._format_comic_caption(item, config)
            kwargs = {
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_message(**kwargs)
            return

        # Обычный медиапост
        processed_media = await media_processor.process_batch(item["media_files"])
        if not processed_media:
            raise Exception("Не удалось обработать медиа")

        caption = self._format_caption(item, config)

        if len(processed_media) == 1:
            media_type, file_path = processed_media[0]
            await media_processor.send_single_media(
                chat_id=chat_id,
                media_type=media_type,
                file_path=file_path,
                caption=caption,
                thread_id=thread_id,
            )
        else:
            await media_processor.send_media_group(
                chat_id=chat_id,
                media_paths=processed_media,
                caption=caption,
                thread_id=thread_id,
            )

        paths = [path for _, path in processed_media]
        media_processor.cleanup_specific(paths)

    # ──────────────────────────────────────────────────────
    #  Форматирование подписей
    # ──────────────────────────────────────────────────────

    def _format_caption(self, item: Dict, config: Dict) -> str:
        category_data = item["category_data"]
        selected_hashtags = item["selected_hashtags"]
        platform_id = item["platform_id"]

        emoji_row = category_data.get("emoji_row", "")
        category_title = (
            f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}"
        )

        hashtag_lines = [
            " ".join(selected_hashtags[i : i + 3])
            for i in range(0, len(selected_hashtags), 3)
        ]
        hashtags_formatted = "\n".join(hashtag_lines)

        invite_link = config["platforms"][platform_id].get(
            "invite_link", "https://t.me/your_channel"
        )

        caption = f"{category_title}\n\n"
        caption += f"{emoji_row}\n"
        caption += f"{hashtags_formatted}\n"
        caption += f"{emoji_row}\n\n"
        if item.get("description"):
            caption += f"{item['description']}\n\n"
        caption += "↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        return caption

    def _format_comic_caption(self, item: Dict, config: Dict) -> str:
        category_data = item["category_data"]
        selected_hashtags = item.get("selected_hashtags", [])
        platform_id = item["platform_id"]
        comic_title = item.get("comic_title", "")
        comic_link = item.get("comic_link", "")

        emoji = category_data["emoji"]
        name = category_data["name"].upper()
        emoji_row = category_data.get("emoji_row", "")

        invite_link = config["platforms"][platform_id].get(
            "invite_link", "https://t.me/your_channel"
        )

        caption = f"{emoji} {name} {emoji}\n\n"
        caption += f"{emoji_row}\n\n"
        caption += f'📖 <b><u><a href="{comic_link}">{comic_title}</a></u></b>\n\n'

        if selected_hashtags:
            hashtag_lines = [
                " ".join(selected_hashtags[i : i + 3])
                for i in range(0, len(selected_hashtags), 3)
            ]
            caption += "\n".join(hashtag_lines) + "\n\n"

        caption += f"{emoji_row}\n\n"
        caption += "↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        return caption
