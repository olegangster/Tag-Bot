import asyncio
import random
from datetime import datetime, timedelta
from typing import List, Tuple, Dict
from pathlib import Path
import logging
import json
import pytz

logger = logging.getLogger(__name__)

KYIV_TZ = pytz.timezone('Europe/Kiev')


def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)


class BatchPostingService:
    """
    Сервис для пакетной публикации постов
    ✅ ИСПРАВЛЕНО: все datetime timezone-aware (Киев)
    """

    def __init__(self, media_processor, bot):
        self.media_processor = media_processor
        self.bot = bot
        self.config = load_config()
        self.scheduled_tasks = {}

    def split_into_posts(self, media_list: List[Tuple[str, Path]], files_per_post: int = 1) -> List[List[Tuple[str, Path]]]:
        posts = []
        for i in range(0, len(media_list), files_per_post):
            posts.append(media_list[i:i + files_per_post])
        return posts

    def randomize_posts(self, posts: List[List[Tuple[str, Path]]], shuffle: bool = False) -> List[List[Tuple[str, Path]]]:
        if shuffle:
            random.shuffle(posts)
        return posts

    def generate_schedule(self, posts_count: int, time_period: str, time_value: int) -> List[datetime]:
        """
        Генерирует расписание для постов.
        ✅ ИСПРАВЛЕНО: все datetime timezone-aware (Киев)
        """
        now = datetime.now(KYIV_TZ)
        schedule = []

        if time_period == 'minute':
            total_minutes = time_value
        elif time_period == 'hour':
            total_minutes = time_value * 60
        elif time_period == 'day':
            total_minutes = time_value * 24 * 60
        elif time_period == 'week':
            total_minutes = time_value * 7 * 24 * 60
        else:
            total_minutes = 60

        if posts_count > 1:
            interval_minutes = total_minutes / (posts_count - 1)
        else:
            interval_minutes = 0

        for i in range(posts_count):
            if i == 0:
                post_time = now + timedelta(minutes=1)
            else:
                post_time = schedule[0] + timedelta(minutes=interval_minutes * i)
            schedule.append(post_time)

        return schedule

    async def schedule_batch(self,
                             posts: List[List[Tuple[str, Path]]],
                             category_data: Dict,
                             platform_id: str,
                             hashtags: List[str],
                             schedule: List[datetime],
                             thread_id: int = None,
                             shuffle: bool = False,
                             batch_id: str = None) -> int:
        posts = self.randomize_posts(posts, shuffle=shuffle)

        if not batch_id:
            import uuid
            batch_id = str(uuid.uuid4())[:8]

        if batch_id not in self.scheduled_tasks:
            self.scheduled_tasks[batch_id] = []

        chat_id = self.config['platforms'][platform_id]['chat_id']
        scheduled_count = 0
        caption = self._format_caption(category_data, hashtags, platform_id)

        if len(posts) > 0:
            try:
                if len(posts[0]) == 1:
                    media_type, file_path = posts[0][0]
                    await self.media_processor.send_single_media(
                        chat_id=chat_id, media_type=media_type, file_path=file_path,
                        caption=caption, thread_id=thread_id
                    )
                else:
                    await self.media_processor.send_media_group(
                        chat_id=chat_id, media_paths=posts[0], caption=caption, thread_id=thread_id
                    )

                scheduled_count += 1
                logger.info(f"Опубликован пост 1/{len(posts)}")
                paths = [path for _, path in posts[0]]
                self.media_processor.cleanup_specific(paths)

            except Exception as e:
                logger.error(f"Ошибка публикации первого поста: {e}")

        for i in range(1, len(posts)):
            task = asyncio.create_task(
                self._schedule_single_post(
                    post_index=i,
                    post_data=posts[i],
                    scheduled_time=schedule[i],
                    chat_id=chat_id,
                    caption=caption,
                    thread_id=thread_id
                )
            )
            self.scheduled_tasks[batch_id].append(task)
            scheduled_count += 1

            logger.info(f"Запланирован пост {i+1}/{len(posts)} на {schedule[i].strftime('%d.%m.%Y %H:%M')}")

        logger.info(f"✅ Пакет {batch_id}: запланировано {scheduled_count} постов")
        return scheduled_count

    async def _schedule_single_post(self,
                                    post_index: int,
                                    post_data: List[Tuple[str, Path]],
                                    scheduled_time: datetime,
                                    chat_id: int,
                                    caption: str,
                                    thread_id: int = None):
        try:
            # Нормализуем timezone
            if isinstance(scheduled_time, datetime):
                if scheduled_time.tzinfo is None:
                    scheduled_time = KYIV_TZ.localize(scheduled_time)
                now_aware = datetime.now(KYIV_TZ)
                wait_seconds = (scheduled_time - now_aware).total_seconds()
            else:
                wait_seconds = 0

            if wait_seconds > 0:
                logger.info(f"Пост {post_index + 1} ожидает {wait_seconds:.0f} секунд")
                await asyncio.sleep(wait_seconds)

            if len(post_data) == 1:
                media_type, file_path = post_data[0]
                await self.media_processor.send_single_media(
                    chat_id=chat_id, media_type=media_type, file_path=file_path,
                    caption=caption, thread_id=thread_id
                )
            else:
                await self.media_processor.send_media_group(
                    chat_id=chat_id, media_paths=post_data, caption=caption, thread_id=thread_id
                )

            logger.info(f"✅ Пост {post_index + 1} успешно опубликован в {datetime.now(KYIV_TZ).strftime('%H:%M:%S')}")

            paths = [path for _, path in post_data]
            self.media_processor.cleanup_specific(paths)

        except asyncio.CancelledError:
            logger.warning(f"Публикация поста {post_index + 1} отменена")
        except Exception as e:
            logger.error(f"Ошибка отложенной публикации поста {post_index + 1}: {e}")

    def _format_caption(self, category_data: Dict, hashtags: List[str], platform_id: str) -> str:
        emoji_row = category_data.get('emoji_row', '')
        category_title = f"{category_data['emoji']} {category_data['name'].upper()} {category_data['emoji']}"

        hashtag_rows = []
        for i in range(0, len(hashtags), 3):
            row = ' '.join(hashtags[i:i+3])
            hashtag_rows.append(row)
        hashtags_formatted = '\n'.join(hashtag_rows)

        platform_data = self.config['platforms'][platform_id]
        invite_link = platform_data.get('invite_link', 'https://t.me/your_channel')
        platform_type = platform_data.get('type', 'group')
        referral_bot = platform_data.get('referral_bot', '@Tag_Referral_bot')

        caption = f"{category_title}\n\n"
        caption += f"{emoji_row}\n"
        caption += f"{hashtags_formatted}\n"
        caption += f"{emoji_row}\n\n"

        if platform_type == 'channel':
            caption += f"Получить бесплатно доступ к группе ➡️ {referral_bot}\n"

        caption += f"↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'

        return caption

    def get_batch_summary(self, posts_count: int, schedule: List[datetime],
                          time_period: str, time_value: int) -> str:
        """Создает текстовое описание пакета"""

        if time_period == 'minute':
            if time_value == 1:
                period_str = "1 минута"
            elif time_value < 5:
                period_str = f"{time_value} минуты"
            else:
                period_str = f"{time_value} минут"
        elif time_period == 'hour':
            if time_value == 1:
                period_str = "1 час"
            elif time_value < 5:
                period_str = f"{time_value} часа"
            else:
                period_str = f"{time_value} часов"
        elif time_period == 'day':
            if time_value == 1:
                period_str = "1 день"
            elif time_value < 5:
                period_str = f"{time_value} дня"
            else:
                period_str = f"{time_value} дней"
        elif time_period == 'week':
            if time_value == 1:
                period_str = "1 неделя"
            elif time_value < 5:
                period_str = f"{time_value} недели"
            else:
                period_str = f"{time_value} недель"
        elif time_period == 'custom':
            period_str = f"кастомный график ({time_value} дней)"
        else:
            period_str = "1 час"

        if not schedule:
            summary = f"📦 <b>Пакетная публикация</b>\n\n"
            summary += f"📊 Постов в пакете: <b>{posts_count}</b>\n"
            summary += f"⏰ Период публикации: <b>{period_str}</b>\n"
            return summary

        if posts_count > 1 and len(schedule) > 1:
            s0 = schedule[0] if isinstance(schedule[0], datetime) else datetime.fromisoformat(schedule[0])
            s_last = schedule[-1] if isinstance(schedule[-1], datetime) else datetime.fromisoformat(schedule[-1])
            interval = (s_last - s0) / (posts_count - 1)
            interval_minutes = int(interval.total_seconds() / 60)
            if interval_minutes < 60:
                interval_str = f"{interval_minutes} мин"
            else:
                interval_hours = interval_minutes / 60
                interval_str = f"{interval_hours:.1f} ч"
        else:
            interval_str = "—"

        def _fmt_dt(dt) -> str:
            if isinstance(dt, str):
                try:
                    dt = datetime.fromisoformat(dt)
                except Exception:
                    return str(dt)
            if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
                dt = dt.astimezone(KYIV_TZ)
            return dt.strftime('%d.%m %H:%M')

        summary = f"📦 <b>Пакетная публикация</b>\n\n"
        summary += f"📊 Постов в пакете: <b>{posts_count}</b>\n"
        summary += f"⏰ Период публикации: <b>{period_str}</b>\n"
        summary += f"📈 Интервал между постами: <b>~{interval_str}</b>\n\n"
        summary += f"🕐 Первый пост: <b>{_fmt_dt(schedule[0])}</b>\n"

        if posts_count > 1 and len(schedule) > 1:
            summary += f"🕐 Последний пост: <b>{_fmt_dt(schedule[-1])}</b>\n"

        return summary

    def cancel_batch_tasks(self, batch_id: str) -> int:
        if batch_id not in self.scheduled_tasks:
            return 0

        cancelled_count = 0
        for task in self.scheduled_tasks[batch_id]:
            if not task.done():
                task.cancel()
                cancelled_count += 1

        del self.scheduled_tasks[batch_id]
        logger.info(f"Отменено {cancelled_count} постов пакета {batch_id}")
        return cancelled_count

    def cancel_all_scheduled(self):
        total_cancelled = 0
        for batch_id in list(self.scheduled_tasks.keys()):
            total_cancelled += self.cancel_batch_tasks(batch_id)
        logger.info(f"Всего отменено постов: {total_cancelled}")
        return total_cancelled
