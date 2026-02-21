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
    """
    
    def __init__(self, filepath: str = "queue.json"):
        self.filepath = Path(filepath)
        self.queue = self._load()
        self.running = False
        self.current_task = None
        self.interval_minutes = 10 
        self.published_count = 0
        self.batch_info = {} 
    
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
            'status': 'pending'
        }
        
        self.queue.append(item)
        self._save()
        logger.info(f"Добавлен в очередь: {item_id} ({name})")
        return item_id

    # (Методы get_status, start_queue и т.д. остаются как были)
    # ...

    async def _publish_post(self, item, media_processor, bot, config):
        """Публикует один пост с поддержкой тем"""
        platform_id = item['platform_id']
        platform_cfg = config['platforms'][platform_id]
        chat_id = int(platform_cfg['chat_id'])
        thread_id = None
    
        # Находим thread_id через ключ топика
        topic_key = item.get('topic')
        if topic_key and platform_cfg.get('type') != 'channel':
            topics = platform_cfg.get('topics', {})
            if topic_key in topics:
                t_id = topics[topic_key].get('thread_id')
                if t_id is not None:
                    thread_id = int(t_id)
    
        # Комикс
        if item.get('is_comic', False):
            caption = self._format_comic_caption(item, config)
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode='HTML',
                disable_web_page_preview=False,
                message_thread_id=thread_id
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

    # ... (остальные методы)
