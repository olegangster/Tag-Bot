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
                    comic_title: str = "") -> str:
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
            'status': 'pending'  # pending, publishing, published, failed
        }
        
        self.queue.append(item)
        self._save()
        logger.info(f"Добавлен в очередь: {item_id} ({name})")
        return item_id
    
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
        
        text = "📥 <b>Статус очереди</b>\n\n"
        text += f"📊 Всего в очереди: {status['total']}\n"
        text += f"⏳ Ожидают: {status['pending']}\n"
        text += f"✅ Опубликовано: {status['published']}\n\n"
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
        """Обрабатывает очередь постов"""
        try:
            config_path = Path('config.json')
            if not config_path.exists():
                logger.error("config.json не найден")
                return
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            for item in self.queue:
                if not self.running:
                    break
                
                if item.get('status') != 'pending':
                    continue
                
                # Обновляем статус
                item['status'] = 'publishing'
                self._save()
                
                try:
                    # Получаем chat_id
                    platform_id = item['platform_id']
                    chat_id = config['platforms'][platform_id]['chat_id']
                    thread_id = None
                    
                    if item.get('topic'):
                        thread_id = config['platforms'][platform_id]['topics'][item['topic']].get('thread_id')
                    
                    # Проверяем, это комикс или обычный пост
                    if item.get('is_comic', False):
                        # Публикация комикса
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
                    else:
                        # Обычный пост с медиа
                        # Обрабатываем медиа
                        processed_media = await media_processor.process_batch(item['media_files'])
                        
                        if not processed_media:
                            item['status'] = 'failed'
                            logger.error(f"Не удалось обработать медиа для {item['name']}")
                            continue
                        
                        # Формируем caption
                        caption = self._format_caption(item, config)
                        
                        # Публикуем
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
            logger.error(f"Ошибка обработки очереди: {e}")
        finally:
            self.running = False
    
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
