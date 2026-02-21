import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class QueueManager:
    def __init__(self, filepath: str = "queue.json"):
        self.filepath = Path(filepath)
        self.queue = self._load()
        self.running = False
        self.current_task = None
        self.interval_minutes = 10
        self.published_count = 0
        self.batch_info = {}

    def _load(self) -> List:
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: return []
        return []

    def _save(self):
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.queue, f, ensure_ascii=False, indent=2)
        except Exception as e: logger.error(f"Save error: {e}")

    def add_to_queue(self, user_id, name, category_key, category_data, platform_id, media_files, selected_hashtags, description="", topic=None, is_comic=False, comic_link="", comic_title="", scheduled_time=None, batch_id=None):
        item = {'id': str(uuid.uuid4())[:8], 'user_id': user_id, 'name': name, 'category_key': category_key, 'category_data': category_data, 'category_name': category_data.get('name', ''), 'platform_id': platform_id, 'topic': topic, 'media_files': media_files, 'selected_hashtags': selected_hashtags, 'description': description, 'is_comic': is_comic, 'comic_link': comic_link, 'comic_title': comic_title, 'added_at': datetime.now().isoformat(), 'scheduled_time': scheduled_time.isoformat() if scheduled_time else None, 'batch_id': batch_id, 'status': 'pending'}
        self.queue.append(item)
        self._save()
        return item['id']

    def get_status(self):
        return {'total': len(self.queue), 'pending': len([i for i in self.queue if i['status']=='pending']), 'published': len([i for i in self.queue if i['status']=='published']), 'running': self.running, 'interval': self.interval_minutes}

    def get_status_text(self):
        s = self.get_status()
        return f"📥 <b>Статус очереди</b>\n\n⏳ Ожидают: {s['pending']}\n✅ Опубликовано: {s['published']}\n⏱ Интервал: {s['interval']} мин\n▶️ Статус: {'Работает' if s['running'] else 'Остановлена'}"

    async def start_queue(self, media_processor, bot):
        if self.running or not [i for i in self.queue if i['status']=='pending']: return False
        self.running = True
        self.current_task = asyncio.create_task(self._process_queue(media_processor, bot))
        return True

    async def _process_queue(self, media_processor, bot):
        try:
            with open('config.json', 'r', encoding='utf-8') as f: config = json.load(f)
            for item in self.queue:
                if not self.running: break
                if item['status'] != 'pending': continue
                item['status'] = 'publishing'; self._save()
                try:
                    await self._publish_post(item, media_processor, bot, config)
                    item['status'] = 'published'; item['published_at'] = datetime.now().isoformat()
                    self._save()
                    if self.running: await asyncio.sleep(self.interval_minutes * 60)
                except Exception as e:
                    item['status'] = 'failed'; item['error'] = str(e); self._save()
                    logger.error(f"Queue post error: {e}")
        finally: self.running = False

    async def _publish_post(self, item, media_processor, bot, config):
        platform_id = item['platform_id']
        chat_id = int(config['platforms'][platform_id]['chat_id'])
        thread_id = None
        
        # РЕШЕНИЕ: Ищем актуальный thread_id из конфига по ключу топика
        topic_key = item.get('topic')
        if topic_key and config['platforms'][platform_id].get('type') != 'channel':
            topics = config['platforms'][platform_id].get('topics', {})
            if topic_key in topics:
                thread_id = int(topics[topic_key]['thread_id'])

        if item.get('is_comic'):
            caption = self._format_caption(item, config)
            await bot.send_message(chat_id=chat_id, text=caption, parse_mode='HTML', message_thread_id=thread_id)
        else:
            processed = await media_processor.process_batch(item['media_files'])
            caption = self._format_caption(item, config)
            if len(processed) == 1:
                await media_processor.send_single_media(chat_id, processed[0][0], processed[0][1], caption, thread_id)
            else:
                await media_processor.send_media_group(chat_id, processed, caption, thread_id)
            media_processor.cleanup_specific([p[1] for p in processed])

    def _format_caption(self, item, config):
        cat = item['category_data']
        tags = item['selected_hashtags']
        invite = config['platforms'][item['platform_id']].get('invite_link', '')
        h_lines = [' '.join(tags[i:i+3]) for i in range(0, len(tags), 3)]
        return f"{cat['emoji']} {cat['name'].upper()} {cat['emoji']}\n\n{cat.get('emoji_row','')}\n{'\n'.join(h_lines)}\n{cat.get('emoji_row','')}\n\n↘️⬇️↙️\n<a href='{invite}'>Ссылка для друга</a>"
