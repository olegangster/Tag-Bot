"""
queue_manager.py — менеджер очереди публикаций
Поддерживает:
- Обычные посты (одиночные медиа)
- Пакетные посты (batch) с расписанием
- Перемешивание порядка (shuffle)
"""
import asyncio
import json
import logging
import random
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def load_config() -> dict:
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


class QueueManager:
    """Менеджер очереди публикаций"""

    def __init__(self, filepath: str = "queue.json"):
        self.filepath = Path(filepath)
        self._queue: List[Dict] = self._load()
        self.running: bool = False
        self.interval: int = 60          # минут между постами (для обычных постов)
        self._task: Optional[asyncio.Task] = None

    # ========== СОХРАНЕНИЕ / ЗАГРУЗКА ==========

    def _load(self) -> List[Dict]:
        if self.filepath.exists():
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Конвертируем строки schedule обратно в datetime
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
            # Сериализуем datetime в строки
            data = []
            for item in self._queue:
                item_copy = dict(item)
                if "schedule" in item_copy:
                    item_copy["schedule"] = [
                        s.isoformat() if isinstance(s, datetime) else s
                        for s in item_copy["schedule"]
                    ]
                data.append(item_copy)

            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения очереди: {e}")

    # ========== ДОБАВЛЕНИЕ В ОЧЕРЕДЬ ==========

    def add_to_queue(
        self,
        user_id: int,
        name: str,
        category_key: str,
        category_data: Dict,
        platform_id: str,
        media_files: List,
        selected_hashtags: List[str],
        description: str = "",
        topic: Optional[str] = None,
        is_comic: bool = False,
        comic_link: str = "",
        comic_title: str = "",
    ) -> str:
        """Добавляет одиночный пост в очередь"""
        item_id = str(uuid.uuid4())[:8]
        self._queue.append({
            "id": item_id,
            "type": "single",
            "user_id": user_id,
            "name": name,
            "category_key": category_key,
            "category_data": category_data,
            "platform_id": platform_id,
            "media_files": media_files,
            "selected_hashtags": selected_hashtags,
            "description": description,
            "topic": topic,
            "is_comic": is_comic,
            "comic_link": comic_link,
            "comic_title": comic_title,
            "created_at": datetime.now().isoformat(),
        })
        self._save()
        logger.info(f"Добавлен пост '{name}' в очередь (id={item_id})")
        return item_id

    def add_batch_to_queue(
        self,
        user_id: int,
        category_data: Dict,
        category_key: str,
        platform_id: str,
        posts: List[List[Tuple[str, str]]],  # list of [(media_type, file_id), ...]
        selected_hashtags: List[str],
        schedule: List[datetime],
        topic: Optional[str],
        time_period: str,
        time_value: int,
        shuffle: bool = False,
    ) -> str:
        """Добавляет пакет постов с расписанием в очередь"""
        batch_id = str(uuid.uuid4())[:8]

        # Перемешиваем если нужно
        if shuffle:
            combined = list(zip(posts, schedule))
            random.shuffle(combined)
            # Восстанавливаем оригинальное расписание но с новым порядком постов
            shuffled_posts = [p for p, _ in combined]
        else:
            shuffled_posts = posts

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
            "created_at": datetime.now().isoformat(),
        })
        self._save()
        logger.info(f"Добавлен пакет #{batch_id[:4]} ({len(posts)} постов) в очередь")
        return batch_id

    # ========== УПРАВЛЕНИЕ ОЧЕРЕДЬЮ ==========

    def get_queue(self) -> List[Dict]:
        return list(self._queue)

    def remove_from_queue(self, index: int):
        if 0 <= index < len(self._queue):
            item = self._queue.pop(index)
            self._save()
            logger.info(f"Удален пост из очереди: {item.get('name', index)}")

    def clear_queue(self):
        self._queue.clear()
        self._save()
        logger.info("Очередь очищена")

    def set_interval(self, minutes: int):
        self.interval = max(1, int(minutes))
        logger.info(f"Интервал очереди установлен: {self.interval} мин")

    def get_status(self) -> Dict:
        return {
            "running": self.running,
            "pending": len(self._queue),
            "interval": self.interval,
        }

    def get_status_text(self) -> str:
        status = self.get_status()
        running_icon = "🟢 Активна" if status["running"] else "🔴 Остановлена"
        text = (
            f"📥 <b>Очередь публикаций</b>\n\n"
            f"Статус: <b>{running_icon}</b>\n"
            f"В очереди: <b>{status['pending']}</b> постов\n"
            f"Интервал: <b>{status['interval']} мин</b>\n"
        )
        return text

    # ========== ЗАПУСК / ОСТАНОВКА ==========

    async def start_queue(self, media_processor, bot) -> bool:
        """Запускает обработку очереди"""
        if not self._queue:
            return False

        if self.running and self._task and not self._task.done():
            return True  # уже запущена

        self.running = True
        self._task = asyncio.create_task(
            self._process_queue_loop(media_processor, bot)
        )
        logger.info("Очередь запущена")
        return True

    async def stop_queue(self):
        """Останавливает очередь"""
        self.running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Очередь остановлена")

    # ========== ОБРАБОТКА ОЧЕРЕДИ ==========

    async def _process_queue_loop(self, media_processor, bot):
        """Основной цикл очереди"""
        from media_processor import MediaProcessor
        from batch_service import BatchPostingService

        logger.info("▶️ Цикл очереди запущен")

        while self.running and self._queue:
            item = self._queue[0]

            try:
                if item["type"] == "batch":
                    await self._publish_batch(item, media_processor, bot)
                else:
                    await self._publish_single(item, media_processor, bot)

                # Удаляем опубликованный
                if self._queue and self._queue[0].get("id") == item.get("id"):
                    self._queue.pop(0)
                    self._save()

            except asyncio.CancelledError:
                logger.info("Очередь отменена")
                break
            except Exception as e:
                logger.error(f"Ошибка публикации из очереди: {e}")
                # Убираем "сломанный" пост чтобы не зависнуть
                if self._queue and self._queue[0].get("id") == item.get("id"):
                    self._queue.pop(0)
                    self._save()

            # Ждём интервал перед следующим постом
            if self._queue and self.running:
                wait_sec = self.interval * 60
                logger.info(f"⏳ Ожидание {self.interval} мин до следующего поста")
                await asyncio.sleep(wait_sec)

        self.running = False
        logger.info("✅ Очередь завершена")

    async def _publish_single(self, item: Dict, media_processor, bot):
        """Публикует одиночный пост"""
        from batch_service import BatchPostingService

        category_data = item["category_data"]
        platform_id = item["platform_id"]
        topic = item.get("topic")
        selected_hashtags = item.get("selected_hashtags", [])
        is_comic = item.get("is_comic", False)

        # Получаем chat_id и thread_id
        config = load_config()
        platform_cfg = config.get("platforms", {}).get(platform_id, {})
        chat_id = int(platform_cfg.get("chat_id", 0))
        thread_id = None
        if topic:
            topics = platform_cfg.get("topics", {})
            thread_id = topics.get(topic, {}).get("thread_id")
            if thread_id:
                thread_id = int(thread_id)

        if is_comic:
            # Публикуем комикс (текстовый пост)
            caption = self._format_caption(category_data, selected_hashtags, platform_id)
            comic_link = item.get("comic_link", "")
            comic_title = item.get("comic_title", "")
            invite_link = platform_cfg.get("invite_link", "https://t.me/your_channel")

            caption = (
                f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}\n\n"
                f"{category_data.get('emoji_row', '')}\n\n"
                f'📖 <b><u><a href="{comic_link}">{comic_title}</a></u></b>\n\n'
                f"{category_data.get('emoji_row', '')}\n\n"
                f"↘️⬇️↙️\n"
                f'<a href="{invite_link}">Ссылка для друга</a>'
            )

            kwargs = dict(chat_id=chat_id, text=caption, parse_mode="HTML")
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_message(**kwargs)
        else:
            # Обрабатываем медиа
            media_files = item.get("media_files", [])
            media_to_process = [(mtype, fid) for mtype, fid, *_ in media_files]
            processed = await media_processor.process_batch(media_to_process)

            if not processed:
                logger.error(f"Не удалось обработать медиа для поста {item.get('name')}")
                return

            caption = self._format_caption(category_data, selected_hashtags, platform_id)

            if len(processed) == 1:
                await media_processor.send_single_media(
                    chat_id, processed[0][0], processed[0][1], caption, thread_id
                )
            else:
                await media_processor.send_media_group(
                    chat_id, processed, caption, thread_id
                )

            paths = [p for _, p in processed]
            media_processor.cleanup_specific(paths)

        logger.info(f"✅ Опубликован пост '{item.get('name')}' из очереди")

    async def _publish_batch(self, item: Dict, media_processor, bot):
        """Публикует пакет постов по расписанию"""
        posts = item.get("posts", [])
        schedule = item.get("schedule", [])
        category_data = item["category_data"]
        platform_id = item["platform_id"]
        topic = item.get("topic")
        selected_hashtags = item.get("selected_hashtags", [])

        config = load_config()
        platform_cfg = config.get("platforms", {}).get(platform_id, {})
        chat_id = int(platform_cfg.get("chat_id", 0))
        thread_id = None
        if topic:
            topics = platform_cfg.get("topics", {})
            thread_id = topics.get(topic, {}).get("thread_id")
            if thread_id:
                thread_id = int(thread_id)

        caption = self._format_caption(category_data, selected_hashtags, platform_id)

        for i, (post_media_ids, sched_time) in enumerate(zip(posts, schedule)):
            if not self.running:
                break

            # Ждём нужного времени
            now = datetime.now()
            if isinstance(sched_time, str):
                sched_time = datetime.fromisoformat(sched_time)

            wait_sec = (sched_time - now).total_seconds()
            if wait_sec > 0:
                logger.info(f"⏳ Пост {i+1}/{len(posts)} запланирован на {sched_time.strftime('%H:%M')}, ожидание {wait_sec:.0f}с")
                await asyncio.sleep(wait_sec)

            # Обрабатываем и публикуем
            try:
                # post_media_ids это [(mtype, file_id), ...]
                media_to_process = [(mtype, fid) for mtype, fid, *_ in post_media_ids] if post_media_ids else []
                if not media_to_process:
                    # Уже без unique_id
                    media_to_process = list(post_media_ids)

                processed = await media_processor.process_batch(media_to_process)
                if not processed:
                    logger.error(f"Не удалось обработать медиа для поста {i+1}")
                    continue

                if len(processed) == 1:
                    await media_processor.send_single_media(
                        chat_id, processed[0][0], processed[0][1], caption, thread_id
                    )
                else:
                    await media_processor.send_media_group(
                        chat_id, processed, caption, thread_id
                    )

                paths = [p for _, p in processed]
                media_processor.cleanup_specific(paths)
                logger.info(f"✅ Пост {i+1}/{len(posts)} из пакета #{item['id'][:4]} опубликован")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Ошибка публикации поста {i+1} из пакета: {e}")

    def _format_caption(self, category_data: Dict, hashtags: List[str], platform_id: str) -> str:
        """Форматирует caption для поста"""
        config = load_config()
        emoji_row = category_data.get("emoji_row", "")
        category_title = f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}"

        hashtag_rows = []
        for i in range(0, len(hashtags), 4):
            row = " ".join(hashtags[i:i+4])
            hashtag_rows.append(row)
        hashtags_formatted = "\n".join(hashtag_rows)

        platform_data = config.get("platforms", {}).get(platform_id, {})
        invite_link = platform_data.get("invite_link", "https://t.me/your_channel")

        caption = f"{category_title}\n\n"
        caption += f"{emoji_row}\n"
        if hashtags_formatted:
            caption += f"{hashtags_formatted}\n"
        caption += f"{emoji_row}\n\n"
        caption += f"↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        return caption
