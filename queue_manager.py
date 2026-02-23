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
    Менеджер очереди постов
    
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
        self.interval_minutes = 10  # По умолчанию 10 минут
        self.published_count = 0
        self.batch_info = {}  # Информация о пакетных отправках {batch_id: {...}}
    
    def _load(self) -> List:
        """Загружает очередь из файла"""
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки очереди: {e}")
                return []
        return []
    
    def _save(self):
        """Сохраняет очередь в файл"""
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.queue, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения очереди: {e}")
    
    def add_to_queue(self,
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
                    batch_id: str = None) -> str:
        """Добавляет пост в очередь"""
        
        item_id = str(uuid.uuid4())[:8]
        
        item = {
            'id': item_id,
            'user_id': user_id,
            'name': name,
            'category_key': category_key,
            'category_data': category_data,
            'category_name': category_data.get('name', ''),
            'platform_id': platform_id,
            'topic': topic,
            'media_files': media_files,
            'selected_hashtags': selected_hashtags,
            'description': description,
            'is_comic': is_comic,
            'comic_link': comic_link,
            'comic_title': comic_title,
            'added_at': datetime.now().isoformat(),
            'scheduled_time': scheduled_time.isoformat() if scheduled_time else None,
            'batch_id': batch_id,
            'status': 'pending'  # pending, publishing, published, failed
        }
        
        self.queue.append(item)
        self._save()
        logger.info(f"Добавлен в очередь: {item_id} ({name})")
        return item_id
    
    def add_batch_to_queue(self,
                          user_id: int,
                          category_data: Dict,
                          category_key: str,
                          platform_id: str,
                          posts: List[List[tuple]],  # Список постов (каждый пост - список медиа)
                          selected_hashtags: List[str],
                          schedule: List[datetime],  # Расписание для каждого поста
                          topic: str = None,
                          time_period: str = "minute",
                          time_value: int = 60,
                          shuffle: bool = False) -> str:
        """Добавляет пакет постов в очередь с расписанием"""
        
        batch_id = str(uuid.uuid4())[:8]
        
        # Сохраняем информацию о пакете
        self.batch_info[batch_id] = {
            'id': batch_id,
            'total_posts': len(posts),
            'published': 0,
            'failed': 0,
            'start_time': schedule[0].isoformat() if schedule else None,
            'end_time': schedule[-1].isoformat() if len(schedule) > 1 else None,
            'time_period': time_period,
            'time_value': time_value,
            'category_name': category_data.get('name', ''),
            'created_at': datetime.now().isoformat()
        }
        
        # Перемешиваем посты если нужно
        if shuffle:
            random.shuffle(posts)
            logger.info(f"🔀 Посты перемешаны")
        
        # Добавляем каждый пост в очередь
        for i, (post_media, scheduled_time) in enumerate(zip(posts, schedule)):
            self.add_to_queue(
                user_id=user_id,
                name=f"Пакет #{batch_id[:4]} - пост {i+1}/{len(posts)}",
                category_key=category_key,
                category_data=category_data,
                platform_id=platform_id,
                media_files=post_media,
                selected_hashtags=selected_hashtags,
                topic=topic,
                scheduled_time=scheduled_time,
                batch_id=batch_id
            )
        
        logger.info(f"Пакет {batch_id} добавлен в очередь: {len(posts)} постов")
        return batch_id
    
    def get_queue(self) -> List[Dict]:
        """Получает всю очередь"""
        return self.queue
    
    def get_pending_count(self) -> int:
        """Количество постов ожидающих публикации"""
        return len([item for item in self.queue if item.get('status') == 'pending'])
    
    def get_published_count(self) -> int:
        """Количество опубликованных постов"""
        return len([item for item in self.queue if item.get('status') == 'published'])
    
    def remove_from_queue(self, index: int) -> bool:
        """Удаляет пост из очереди по индексу"""
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
        """Очищает всю очередь"""
        self.queue = []
        self._save()
        self.published_count = 0
        logger.info("Очередь очищена")
    
    def set_interval(self, minutes: int):
        """Устанавливает интервал между постами"""
        self.interval_minutes = max(1, min(minutes, 1440))  # От 1 мин до 24 часов
        logger.info(f"Установлен интервал: {self.interval_minutes} минут")
    
    def get_status(self) -> Dict:
        """Получает статус очереди"""
        total = len(self.queue)
        pending = self.get_pending_count()
        published = self.get_published_count()
        
        return {
            'total': total,
            'pending': pending,
            'published': published,
            'running': self.running,
            'interval': self.interval_minutes
        }
    
    def get_status_text(self) -> str:
        """Форматирует статус очереди"""
        status = self.get_status()
        
        # Собираем информацию о пакетах
        batch_posts = {}
        for item in self.queue:
            if item.get('batch_id'):
                batch_id = item['batch_id']
                if batch_id not in batch_posts:
                    batch_posts[batch_id] = {
                        'total': 0,
                        'published': 0,
                        'pending': 0,
                        'failed': 0,
                        'category': item.get('category_name', ''),
                        'scheduled_times': []
                    }
                batch_posts[batch_id]['total'] += 1
                if item.get('status') == 'published':
                    batch_posts[batch_id]['published'] += 1
                elif item.get('status') == 'pending':
                    batch_posts[batch_id]['pending'] += 1
                    if item.get('scheduled_time'):
                        batch_posts[batch_id]['scheduled_times'].append(
                            datetime.fromisoformat(item['scheduled_time'])
                        )
                elif item.get('status') == 'failed':
                    batch_posts[batch_id]['failed'] += 1
        
        text = "📥 <b>Статус очереди</b>\n\n"
        text += f"📊 Всего в очереди: {status['total']}\n"
        text += f"⏳ Ожидают: {status['pending']}\n"
        text += f"✅ Опубликовано: {status['published']}\n\n"
        
        # Информация о пакетах
        if batch_posts:
            text += "📦 <b>Пакетные отправки:</b>\n\n"
            for batch_id, info in batch_posts.items():
                text += f"🔹 Пакет #{batch_id[:4]}\n"
                text += f"   Категория: {info['category']}\n"
                text += f"   Всего: {info['total']} | "
                text += f"Опубл: {info['published']} | "
                text += f"Ожидают: {info['pending']}\n"
                
                if info['scheduled_times']:
                    next_time = min(info['scheduled_times'])
                    last_time = max(info['scheduled_times'])
                    text += f"   ⏰ След. пост: {next_time.strftime('%d.%m %H:%M')}\n"
                    if info['pending'] > 1:
                        text += f"   🕐 Последний: {last_time.strftime('%d.%m %H:%M')}\n"
                
                if info['failed'] > 0:
                    text += f"   ❌ Ошибок: {info['failed']}\n"
                
                text += "\n"
        
        text += f"⏱ Интервал: {status['interval']} минут\n"
        text += f"▶️ Статус: {'Работает' if status['running'] else 'Остановлена'}"
        
        return text
    
    async def start_queue(self, media_processor, bot):
        """Запускает публикацию очереди"""
        if self.running:
            logger.warning("Очередь уже запущена")
            return False
        
        pending = [item for item in self.queue if item.get('status') == 'pending']
        if not pending:
            logger.warning("Нет постов для публикации")
            return False
        
        self.running = True
        self.published_count = 0
        
        logger.info(f"Запуск очереди: {len(pending)} постов с интервалом {self.interval_minutes} мин")
        
        # Создаем задачу публикации
        self.current_task = asyncio.create_task(
            self._process_queue(media_processor, bot)
        )
        
        return True
    
    async def stop_queue(self):
        """Останавливает публикацию очереди"""
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
    
    async def _process_queue(self, media_processor, bot):
        """Обрабатывает очередь постов с поддержкой расписания"""
        try:
            config_path = Path('config.json')
            if not config_path.exists():
                logger.error("config.json не найден")
                return
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Создаем задачи для постов с расписанием
            scheduled_tasks = []
            
            for item in self.queue:
                if not self.running:
                    break
                
                if item.get('status') != 'pending':
                    continue
                
                # Если есть scheduled_time, создаем отдельную задачу
                if item.get('scheduled_time'):
                    task = asyncio.create_task(
                        self._publish_scheduled_post(item, media_processor, bot, config)
                    )
                    scheduled_tasks.append(task)
            
            # Если есть scheduled посты, ждем их выполнения
            if scheduled_tasks:
                logger.info(f"Запущено {len(scheduled_tasks)} scheduled постов")
                await asyncio.gather(*scheduled_tasks, return_exceptions=True)
            
            # Обрабатываем посты БЕЗ расписания (обычная очередь)
            for item in self.queue:
                if not self.running:
                    break
                
                if item.get('status') != 'pending':
                    continue
                
                # Пропускаем scheduled посты - они уже обрабатываются
                if item.get('scheduled_time'):
                    continue
                
                # Обновляем статус
                item['status'] = 'publishing'
                self._save()
                
                try:
                    await self._publish_post(item, media_processor, bot, config)
                    
                    # Обновляем статус
                    item['status'] = 'published'
                    item['published_at'] = datetime.now().isoformat()
                    self.published_count += 1
                    self._save()
                    
                    logger.info(f"✅ Опубликован из очереди: {item['name']} ({self.published_count})")
                    
                    # Ждем интервал перед следующим постом
                    if self.running:
                        await asyncio.sleep(self.interval_minutes * 60)
                
                except Exception as e:
                    item['status'] = 'failed'
                    item['error'] = str(e)
                    self._save()
                    logger.error(f"Ошибка публикации из очереди: {e}")
        
        except asyncio.CancelledError:
            logger.info("Обработка очереди отменена")
        except Exception as e:
            logger.error(f"Критическая ошибка в _process_queue: {e}")
        finally:
            self.running = False
    
    async def _publish_scheduled_post(self, item, media_processor, bot, config):
        """Публикует пост в запланированное время"""
        try:
            # Рассчитываем время ожидания
            scheduled_time = datetime.fromisoformat(item['scheduled_time'])
            now = datetime.now()
            wait_seconds = (scheduled_time - now).total_seconds()
            
            if wait_seconds > 0:
                logger.info(f"Пост {item['name']} ожидает {wait_seconds:.0f}с до {scheduled_time.strftime('%H:%M:%S')}")
                await asyncio.sleep(wait_seconds)
            
            if not self.running:
                return
            
            # Обновляем статус
            item['status'] = 'publishing'
            self._save()
            
            # Публикуем
            await self._publish_post(item, media_processor, bot, config)
            
            # Обновляем статус
            item['status'] = 'published'
            item['published_at'] = datetime.now().isoformat()
            
            # Обновляем счетчик batch если есть
            if item.get('batch_id') and item['batch_id'] in self.batch_info:
                self.batch_info[item['batch_id']]['published'] += 1
            
            self.published_count += 1
            self._save()
            
            logger.info(f"✅ Scheduled пост опубликован: {item['name']} в {datetime.now().strftime('%H:%M:%S')}")
        
        except asyncio.CancelledError:
            logger.warning(f"Публикация поста {item['name']} отменена")
        except Exception as e:
            item['status'] = 'failed'
            item['error'] = str(e)
            if item.get('batch_id') and item['batch_id'] in self.batch_info:
                self.batch_info[item['batch_id']]['failed'] += 1
            self._save()
            logger.error(f"Ошибка публикации scheduled поста: {e}")
    
    async def _publish_post(self, item, media_processor, bot, config):
        """Публикует один пост"""
        platform_id = item['platform_id']
        chat_id = config['platforms'][platform_id]['chat_id']
        thread_id = None
    
        if item.get('topic'):
            thread_id = config['platforms'][platform_id]['topics'][item['topic']].get('thread_id')
    
        # Комикс
        if item.get('is_comic', False):
            caption = self._format_comic_caption(item, config)
    
            if thread_id:
                await bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode='HTML',
                    disable_web_page_preview=False,
                    message_thread_id=thread_id
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode='HTML',
                    disable_web_page_preview=False
                )
            return
    
        # Обычный пост с медиа
        processed_media = await media_processor.process_batch(item['media_files'])
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
                thread_id=thread_id
            )
        else:
            await media_processor.send_media_group(
                chat_id=chat_id,
                media_paths=processed_media,
                caption=caption,
                thread_id=thread_id
            )
    
        # Очищаем файлы
        paths = [path for _, path in processed_media]
        media_processor.cleanup_specific(paths)

    
    def _format_caption(self, item: Dict, config: Dict) -> str:
        """Форматирует caption для поста из очереди"""
        category_data = item['category_data']
        selected_hashtags = item['selected_hashtags']
        platform_id = item['platform_id']
        
        # Эмодзи ряд
        emoji_row = category_data.get('emoji_row', '')
        
        # Название категории с эмодзи
        emoji = category_data['emoji']
        name = category_data['name'].upper()
        category_title = f"{emoji} {name} {emoji}"
        
        # Форматируем хештеги по 3 в ряд
        hashtag_lines = []
        for i in range(0, len(selected_hashtags), 3):
            line = ' '.join(selected_hashtags[i:i+3])
            hashtag_lines.append(line)
        hashtags_formatted = '\n'.join(hashtag_lines)
        
        # Получаем ссылку-приглашение
        platform_data = config['platforms'][platform_id]
        invite_link = platform_data.get('invite_link', 'https://t.me/your_channel')
        
        # Собираем пост
        caption = f"{category_title}\n\n"
        caption += f"{emoji_row}\n"
        caption += f"{hashtags_formatted}\n"
        caption += f"{emoji_row}\n\n"
        
        # Добавляем описание если есть
        if item.get('description'):
            caption += f"{item['description']}\n\n"
        
        caption += f"↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        
        return caption
    
    def _format_comic_caption(self, item: Dict, config: Dict) -> str:
        """Форматирует caption для комикса из очереди"""
        category_data = item['category_data']
        selected_hashtags = item.get('selected_hashtags', [])
        platform_id = item['platform_id']
        comic_title = item.get('comic_title', '')
        comic_link = item.get('comic_link', '')
        
        emoji = category_data['emoji']
        name = category_data['name'].upper()
        emoji_row = category_data.get('emoji_row', '')
        
        platform_data = config['platforms'][platform_id]
        invite_link = platform_data.get('invite_link', 'https://t.me/your_channel')
        
        # Основной контент - название комикса как гиперссылка
        caption = f"{emoji} {name} {emoji}\n\n"
        caption += f"{emoji_row}\n\n"
        caption += f'📖 <b><u><a href="{comic_link}">{comic_title}</a></u></b>\n\n'
        
        # Хештеги (опционально для комиксов)
        if selected_hashtags:
            hashtag_lines = []
            for i in range(0, len(selected_hashtags), 3):
                line = ' '.join(selected_hashtags[i:i+3])
                hashtag_lines.append(line)
            hashtags_formatted = '\n'.join(hashtag_lines)
            caption += f"{hashtags_formatted}\n\n"
        
        caption += f"{emoji_row}\n\n"
        caption += f"↘️⬇️↙️\n"
        caption += f'<a href="{invite_link}">Ссылка для друга</a>'
        
        return caption

    def get_batch_status(self, batch_id: str) -> Dict:
        """Получает статус конкретного пакета"""
        batch_posts = [item for item in self.queue if item.get("batch_id") == batch_id]
        
        if not batch_posts:
            return None
        
        total = len(batch_posts)
        published = len([p for p in batch_posts if p.get("status") == "published"])
        pending = len([p for p in batch_posts if p.get("status") == "pending"])
        failed = len([p for p in batch_posts if p.get("status") == "failed"])
        
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
            "category_name": batch_posts[0].get("category_name", "")
        }

