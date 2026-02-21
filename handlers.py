import logging
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
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

draft_mgr = DraftManager()
queue_mgr = QueueManager()
user_locks = {}

def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(config):
    with open('config.json', 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

def resolve_chat_and_thread(platform_id: str, topic_key: Optional[str]) -> Tuple[int, Optional[int]]:
    config = load_config()
    platform_cfg = config.get("platforms", {}).get(platform_id)
    if not platform_cfg:
        raise ValueError(f"platform_id '{platform_id}' не найден")

    chat_id = int(platform_cfg["chat_id"])

    # Для каналов темы не используются
    if platform_cfg.get("type") == "channel":
        return chat_id, None

    # Если тема не указана, отправляем в общий поток (без thread_id)
    if not topic_key:
        return chat_id, None

    topics = platform_cfg.get("topics", {})
    topic_cfg = topics.get(topic_key)
    
    if not topic_cfg:
        logger.warning(f"Топик '{topic_key}' не найден в конфигурации для {platform_id}")
        return chat_id, None

    thread_id = topic_cfg.get("thread_id")
    return chat_id, int(thread_id) if thread_id is not None else None

def get_participant_data(user_id: int):
    config = load_config()
    for participant_key, data in config['participants'].items():
        if data.get('user_id') == user_id:
            return participant_key, data
    return None, None

def get_category_data(user_id: int, category_name: str):
    _, participant_data = get_participant_data(user_id)
    if not participant_data:
        return None, None, None
    for cat_key, cat_data in participant_data['categories'].items():
        full_name = f"{cat_data.get('emoji', '📁')} {cat_data['name']}"
        if cat_data['name'] == category_name or full_name == category_name:
            return cat_key, cat_data, participant_data['categories'][cat_key]
    return None, None, None

def format_post_caption(category_data: Dict, selected_hashtags: List[str], platform_id: str, description: str = "") -> str:
    config = load_config()
    emoji = category_data['emoji']
    name = category_data['name'].upper()
    emoji_row = category_data.get('emoji_row', '')
    hashtag_lines = [' '.join(selected_hashtags[i:i+3]) for i in range(0, len(selected_hashtags), 3)]
    hashtags_formatted = '\n'.join(hashtag_lines)
    platform_data = config['platforms'][platform_id]
    invite_link = platform_data.get('invite_link', 'https://t.me/your_channel')
    caption = f"{emoji} {name} {emoji}\n\n{emoji_row}\n"
    if selected_hashtags:
        caption += f"{hashtags_formatted}\n{emoji_row}\n\n"
    if description:
        caption += f"{description}\n\n"
    caption += f"↘️⬇️↙️\n"
    caption += f'<a href="{invite_link}">Ссылка для друга</a>'
    return caption

def format_comic_post(category_data: Dict, comic_title: str, comic_link: str, selected_hashtags: List[str], platform_id: str) -> str:
    config = load_config()
    emoji = category_data['emoji']
    name = category_data['name'].upper()
    emoji_row = category_data.get('emoji_row', '')
    platform_data = config['platforms'][platform_id]
    invite_link = platform_data.get('invite_link', 'https://t.me/your_channel')
    caption = f"{emoji} {name} {emoji}\n\n{emoji_row}\n\n📖 <b><u><a href='{comic_link}'>{comic_title}</a></u></b>\n\n"
    if selected_hashtags:
        hashtag_lines = [' '.join(selected_hashtags[i:i+3]) for i in range(0, len(selected_hashtags), 3)]
        caption += '\n'.join(hashtag_lines) + "\n\n"
    caption += f"{emoji_row}\n\n↘️⬇️↙️\n<a href='{invite_link}'>Ссылка для друга</a>"
    return caption

async def safe_edit_status(message: Message, state: FSMContext, text: str):
    data = await state.get_data()
    status_id = data.get('status_message_id')
    if status_id:
        try:
            await message.bot.edit_message_text(chat_id=message.chat.id, message_id=status_id, text=text, parse_mode='HTML')
            return
        except: pass
    sent = await message.answer(text, parse_mode='HTML')
    await state.update_data(status_message_id=sent.message_id)

@router.message(Command("start"))
@router.message(F.text == "🏠 Главная")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    _, participant_data = get_participant_data(message.from_user.id)
    if not participant_data:
        await message.answer(f"❌ Вы не зарегистрированы. ID: <code>{message.from_user.id}</code>", parse_mode='HTML')
        return
    await message.answer(f"👋 Привет, {participant_data['name']}!", reply_markup=main_menu(), parse_mode='HTML')

@router.message(F.text.in_(["➕ Создать пост", "➕ Ещё пост"]))
async def create_post(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(PostFlow.selecting_category)
    await state.update_data(is_batch_mode=False)
    await message.answer("📁 <b>Выберите категорию:</b>", reply_markup=categories_keyboard(message.from_user.id), parse_mode='HTML')

@router.message(PostFlow.selecting_category)
async def category_selected(message: Message, state: FSMContext):
    if message.text == "🏠 Главная": await cmd_start(message, state); return
    cat_key, cat_data, _ = get_category_data(message.from_user.id, message.text)
    if not cat_data: await message.answer("❌ Категория не найдена"); return
    data = await state.get_data()
    is_batch = data.get('is_batch_mode', False)
    is_comic = cat_data.get('mode') == 'link_post'
    await state.update_data(category_key=cat_key, category_data=cat_data, platform_id=cat_data['platform'], topic=cat_data.get('topic'), media_files=[], selected_hashtags=[], description="", is_comic_mode=is_comic)
    if is_comic and not is_batch:
        await state.set_state(PostFlow.comic_link)
        await message.answer(f"✅ Категория: {cat_data['name']}\n🔗 Отправьте ссылку на комикс:", reply_markup=comic_input_keyboard(), parse_mode='HTML')
    else:
        await state.set_state(PostFlow.batch_uploading if is_batch else PostFlow.uploading_media)
        await message.answer(f"✅ Категория: {cat_data['name']}\n📸 Отправьте медиа:", reply_markup=media_upload_keyboard(0), parse_mode='HTML')

async def handle_media_input(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in user_locks: user_locks[user_id] = asyncio.Lock()
    async with user_locks[user_id]:
        data = await state.get_data()
        media_files = data.get('media_files', [])
        if message.photo: media_files.append(('photo', message.photo[-1].file_id, message.photo[-1].file_unique_id))
        elif message.video: media_files.append(('video', message.video.file_id, message.video.file_unique_id))
        if message.media_group_id:
            import time
            ts = time.time()
            await state.update_data(media_files=media_files, last_media_ts=ts)
            await asyncio.sleep(0.5)
            data2 = await state.get_data()
            if data2.get('last_media_ts') != ts: return
        else: await state.update_data(media_files=media_files)
        await safe_edit_status(message, state, f"✅ Загружено: <b>{len(media_files)}</b> файлов\nНажмите <b>➡️ Далее</b>")

@router.message(PostFlow.uploading_media, F.photo | F.video)
@router.message(PostFlow.batch_uploading, F.photo | F.video)
async def media_received(message: Message, state: FSMContext):
    await handle_media_input(message, state)

@router.message(F.text.startswith("➡️ Далее"))
async def media_done(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get('media_files'): await message.answer("❌ Загрузите файлы"); return
    if await state.get_state() == PostFlow.batch_uploading:
        await state.set_state(PostFlow.batch_schedule)
        await message.answer("⚙️ Сколько файлов в один пост?", reply_markup=files_per_post_keyboard(), parse_mode='HTML')
    else:
        cat_data, cat_key = data['category_data'], data['category_key']
        await state.set_state(PostFlow.selecting_hashtags)
        await message.answer("🏷 <b>Выберите хештеги:</b>", reply_markup=hashtags_keyboard(cat_data.get('hashtags', []), [], message.from_user.id, cat_key), parse_mode='HTML')

@router.callback_query(F.data.startswith("tag:"))
async def hashtag_selection(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get('selected_hashtags', [])
    action = callback.data[4:]
    if action == "done":
        await state.update_data(selected_hashtags=selected)
        await publish_post(callback.message, state, callback.from_user.id)
    elif action in data['category_data']['hashtags']:
        if action in selected: selected.remove(action)
        else: selected.append(action)
        await state.update_data(selected_hashtags=selected)
        await callback.message.edit_reply_markup(reply_markup=hashtags_keyboard(data['category_data']['hashtags'], selected, callback.from_user.id, data['category_key']))
    await callback.answer()

async def publish_post(message: Message, state: FSMContext, user_id: int):
    data = await state.get_data()
    processor = MediaProcessor(bot=message.bot)
    await message.edit_text("⏳ Публикация...")
    processed = await processor.process_batch([(m[0], m[1]) for m in data['media_files']])
    chat_id, thread_id = resolve_chat_and_thread(data['platform_id'], data.get('topic'))
    caption = format_post_caption(data['category_data'], data['selected_hashtags'], data['platform_id'])
    if len(processed) == 1:
        success = await processor.send_single_media(chat_id, processed[0][0], processed[0][1], caption, thread_id)
    else:
        success = await processor.send_media_group(chat_id, processed, caption, thread_id)
    if success:
        await message.edit_text("✅ Опубликовано!")
        await message.answer("Что дальше?", reply_markup=after_post_keyboard())
        await state.clear()
    else: await message.edit_text("❌ Ошибка публикации")

@router.message(F.text == "📥 Очередь")
async def show_queue(message: Message, state: FSMContext):
    status = queue_mgr.get_status()
    await message.answer(queue_mgr.get_status_text(), reply_markup=queue_menu_keyboard(status['running'], status['pending'], status['interval']), parse_mode='HTML')

@router.callback_query(F.data == "q:start")
async def q_start(callback: CallbackQuery, state: FSMContext):
    await queue_mgr.start_queue(MediaProcessor(callback.message.bot), callback.message.bot)
    await callback.answer("✅ Запущено")
    await show_queue(callback.message, state)

@router.message(F.text == "🧹 Очистить чат")
async def clear_chat(message: Message, bot: Bot):
    for i in range(1, 50):
        try: await bot.delete_message(message.chat.id, message.message_id - i)
        except: pass
    await message.answer("✅ Чат очищен", reply_markup=main_menu())
