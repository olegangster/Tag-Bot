import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)

class DraftManager:
    """Менеджер черновиков"""

    def __init__(self, filepath: str = "drafts.json"):
        self.filepath = Path(filepath)
        self.drafts = self._load()

    def _load(self) -> Dict:
        if self.filepath.exists():
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки черновиков: {e}")
                return {}
        return {}

    def _save(self):
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.drafts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения черновиков: {e}")

    def create_draft(self,
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

        draft_id = str(uuid.uuid4())[:8]

        self.drafts[draft_id] = {
            'id': draft_id,
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
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }

        self._save()
        logger.info(f"Создан черновик {draft_id} для пользователя {user_id}")
        return draft_id

    def get_user_drafts(self, user_id: int) -> Dict:
        return {
            did: draft for did, draft in self.drafts.items()
            if draft.get('user_id') == user_id
        }

    def get_draft(self, draft_id: str) -> Optional[Dict]:
        return self.drafts.get(draft_id)

    def update_draft(self, draft_id: str, **kwargs):
        if draft_id in self.drafts:
            self.drafts[draft_id].update(kwargs)
            self.drafts[draft_id]['updated_at'] = datetime.now().isoformat()
            self._save()
            return True
        return False

    def delete_draft(self, draft_id: str) -> bool:
        if draft_id in self.drafts:
            del self.drafts[draft_id]
            self._save()
            return True
        return False

    def get_draft_info(self, draft_id: str) -> str:
        draft = self.get_draft(draft_id)
        if not draft:
            return "Черновик не найден"

        created = datetime.fromisoformat(draft['created_at'])
        files_count = len(draft.get('media_files', []))
        hashtags_count = len(draft.get('selected_hashtags', []))

        info = f"📄 <b>{draft['name']}</b>\n\n"
        info += f"📁 Категория: {draft['category_name']}\n"
        info += f"📸 Файлов: {files_count}\n"
        info += f"#️⃣ Хештегов: {hashtags_count}\n"

        if draft.get('description'):
            desc = draft['description'][:50]
            if len(draft['description']) > 50:
                desc += "..."
            info += f"📝 Описание: {desc}\n"

        info += f"🕐 Создан: {created.strftime('%d.%m.%Y %H:%M')}\n"

        return info
