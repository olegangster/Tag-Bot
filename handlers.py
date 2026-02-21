import logging
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from datetime import datetime
from typing import List, Tuple, Dict, Optional
import json
from pathlib import Path

from states import PostFlow, QueueFlow, HashtagFlow
from keyboards import *
from media_processor import MediaProcessor
from batch_service import BatchPostingService
from draft_manager import DraftManager
from queue_manager import QueueManager

logger = logging.getLogger(__name__)
router = Router()

# Глобальные менеджеры
draft_mgr = DraftManager()
queue_mgr = QueueManager()

# Лок для предотвращения спама при альбомах
user_locks = {}

def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(config):
    """Сохраняет конфигурацию в файл"""
    with open('config.json', 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def resolve_chat_and_thread(platform_id: str, topic_key: Optional[str]) -> Tuple[int, Optional[int]]:
    """
    Возвращает (chat_id:int, thread_id:int|None).
    """
    config = load_config()
    platform_cfg = config.get("platforms", {}).get(platform_id)
    if not platform_cfg:
        raise ValueError(f"platform_id '{platform_id}' не найден в config.json")

    chat_id = int(platform_cfg["chat_id"])

    # topic_key не задан или это канал => шлем без thread_id
    if not topic_key or platform_cfg.get("type") == "channel":
        return chat_id, None

    topics = platform_cfg.get("topics", {})
    topic_cfg = topics.get(topic_key)
    if not topic_cfg:
        # Если ключ не найден, не ломаем отправку, а шлем в основной поток
        logger.warning(f"topic '{topic_key}' не найден в конфиге. Шлем в общий поток.")
        return chat_id, None

    thread_id = topic_cfg.get("thread_id")
    if thread_id is None:
        return chat_id, None

    return chat_id, int(thread_id)


def get_participant_data(user_id: int):
    """Находит данные участника"""
    config = load_config()
    for participant_key, data in config['participants'].items():
        if data.get('user_id') == user_id:
            return participant_key, data
    return None, None

def get_category_data(user_id: int, category_name: str):
    """Получает данные категории"""
    participant_key, participant_data = get_participant_data(user_id)
    if not participant_data:
        return None, None, None
    
    for cat_key, cat_data in participant_data['categories'].items():
        if cat_data['name'] == category_name or f"{cat_data['emoji']} {cat_data['name']}" == category_name:
            return cat_key, cat_data, participant_data['categories'][cat_key]
    return None, None, None

def format_post_caption(category_data: Dict, selected_hashtags: List[str], platform_id: str, description: str = "") -> str:
    """Форматирует caption для обычного поста"""
    config = load_config()
    
    emoji = category_data['emoji']
    name = category_data['name'].upper()
    emoji_row = category_data.get('emoji_row', '')
    
    # Хештеги по 3 в ряд
    hashtag_lines = []
    for i in range(0, len(selected_hashtags), 3):
        line = ' '.join(selected_hashtags[i:i+3])
        hashtag_lines.append(line)
    hashtags_formatted = '\n'.join(hashtag_lines)
    
    platform_data = config['platforms'][platform_id]
    invite_link = platform_data.get('invite_link', 'https://t.me/your_channel')
    
    caption = f"{emoji} {name} {emoji}\n\n"
    caption += f"{emoji_row}\n"
    
    if selected_hashtags:
        caption += f"{hashtags_formatted}\n"
        caption += f"{emoji_row}\n\n"
    
    if description:
        caption += f"{description}\n\n"
    
    caption += f"↘️⬇️↙️\n"
    caption += f'<a href="{invite_link}">Ссылка для друга</a>'
    
    return caption

def format_comic_post(category_data: Dict, comic_title: str, comic_link: str, 
                     selected_hashtags: List[str], platform_id: str) -> str:
    """Форматирует пост для комикса"""
    config = load_config()
    
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

async def safe_edit_status(message: Message, state: FSMContext, text: str):
    """Безопасное редактирование статусного сообщения"""
    data = await state.get_data()
    status_id = data.get('status_message_id')
    
    if status_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=status_id,
                text=text,
                parse_mode='HTML'
            )
            return
        except Exception:
            pass
    
    # Отправляем новое
    sent = await message.answer(text, parse_mode='HTML')
    await state.update_data(status_message_id=sent.message_id)

# ============ ГЛАВНОЕ МЕНЮ ============

@router.message(Command("start"))
@router.message(F.text == "🏠 Главная")
async def cmd_start(message: Message, state: FSMContext):
    """Главное меню"""
    await state.clear()
    
    participant_key, participant_data = get_participant_data(message.from_user.id)
    
    if not participant_data:
        await message.answer(
            "❌ Вы не зарегистрированы в системе.\n"
            f"Ваш ID: <code>{message.from_user.id}</code>\n\n"
            "Обратитесь к администратору.",
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        f"👋 <b>Привет, {participant_data['name']}!</b>\n\n"
        "Выберите действие:",
        reply_markup=main_menu(),
        parse_mode='HTML'
    )

# ============ СОЗДАНИЕ ПОСТА ============

@router.message(F.text.in_(["➕ Создать пост", "➕ Ещё пост"]))
async def create_post(message: Message, state: FSMContext):
    """Начало создания поста"""
    await state.clear()
    await state.set_state(PostFlow.selecting_category)
    await state.update_data(is_batch_mode=False)
    await message.answer(
        "📁 <b>Выберите категорию:</b>",
        reply_markup=categories_keyboard(message.from_user.id),
        parse_mode='HTML'
    )

@router.message(PostFlow.selecting_category)
async def category_selected(message: Message, state: FSMContext):
    """Категория выбрана"""
    if message.text == "🏠 Главная":
        await cmd_start(message, state)
        return
    
    category_name = message.text
    cat_key, cat_data, _ = get_category_data(message.from_user.id, category_name)
    
    if not cat_data:
        await message.answer("❌ Категория не найдена")
        return
    
    data = await state.get_data()
    is_batch = data.get('is_batch_mode', False)
    
    # Проверяем, это комиксы или обычный пост
    is_comic_mode = cat_data.get('mode') == 'link_post'
    
    await state.update_data(
        category_key=cat_key,
        category_data=cat_data,
        platform_id=cat_data['platform'],
        topic=cat_data.get('topic'),
        media_files=[],
        selected_hashtags=[],
        description="",
        is_comic_mode=is_comic_mode
    )
    
    if is_comic_mode and not is_batch:
        # Для комиксов - запрашиваем ссылку
        await state.set_state(PostFlow.comic_link)
        await message.answer(
            f"✅ Категория: <b>{cat_data['emoji']} {cat_data['name']}</b>\n\n"
            "🔗 <b>Отправьте ссылку на комикс:</b>\n"
            "(Например: https://readcomiconline.li/Comic/...)",
            reply_markup=comic_input_keyboard(),
            parse_mode='HTML'
        )
    else:
        # Обычный режим загрузки медиа
        next_state = PostFlow.batch_uploading if is_batch else PostFlow.uploading_media
        await state.set_state(next_state)
        
        mode = "пакетной загрузки" if is_batch else "поста"
        await message.answer(
            f"✅ Категория: <b>{cat_data['emoji']} {cat_data['name']}</b>\n\n"
            f"📸 Отправьте фото или видео для {mode}:",
            reply_markup=media_upload_keyboard(0),
            parse_mode='HTML'
        )

# (Остальные хендлеры handlers.py остаются без изменений)
# ...
