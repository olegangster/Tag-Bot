import logging
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from datetime import datetime
from typing import List, Tuple, Dict
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

# ============ РЕЖИМ КОМИКСОВ ============

@router.message(PostFlow.comic_link)
async def comic_link_received(message: Message, state: FSMContext):
    """Получена ссылка на комикс"""
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return
    
    # Простая валидация ссылки
    if not message.text.startswith(('http://', 'https://')):
        await message.answer(
            "❌ Пожалуйста, отправьте корректную ссылку, начинающуюся с http:// или https://",
            reply_markup=comic_input_keyboard()
        )
        return
    
    await state.update_data(comic_link=message.text)
    await state.set_state(PostFlow.comic_title)
    
    await message.answer(
        "✅ Ссылка сохранена!\n\n"
        "📝 <b>Теперь введите название комикса:</b>\n"
        "(Например: Spider-Man #1)",
        reply_markup=comic_input_keyboard(),
        parse_mode='HTML'
    )

@router.message(PostFlow.comic_title)
async def comic_title_received(message: Message, state: FSMContext):
    """Получено название комикса"""
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return
    
    await state.update_data(comic_title=message.text)
    
    data = await state.get_data()
    comic_link = data.get('comic_link', '')
    comic_title = message.text
    category_data = data['category_data']
    platform_id = data['platform_id']
    
    # Показываем превью
    preview = format_comic_post(
        category_data=category_data,
        comic_title=comic_title,
        comic_link=comic_link,
        selected_hashtags=[],
        platform_id=platform_id
    )
    
    await message.answer(
        "📋 <b>Превью поста:</b>\n\n" + "─" * 30,
        parse_mode='HTML'
    )
    
    await message.answer(
        preview,
        parse_mode='HTML',
        disable_web_page_preview=True
    )
    
    await message.answer(
        "─" * 30 + "\n\n"
        "Что дальше?",
        reply_markup=comic_confirm_keyboard(),
        parse_mode='HTML'
    )

@router.callback_query(F.data.startswith("comic:"))
async def comic_actions(callback: CallbackQuery, state: FSMContext):
    """Действия с комиксом"""
    action = callback.data.split(":")[1]
    
    if action == "publish":
        await callback.answer("📤 Публикуем...")
        
        data = await state.get_data()
        category_data = data['category_data']
        platform_id = data['platform_id']
        topic = data.get('topic')
        comic_link = data.get('comic_link', '')
        comic_title = data.get('comic_title', '')
        selected_hashtags = data.get('selected_hashtags', [])
        
        # Формируем caption
        caption = format_comic_post(
            category_data=category_data,
            comic_title=comic_title,
            comic_link=comic_link,
            selected_hashtags=selected_hashtags,
            platform_id=platform_id
        )
        
        # Получаем chat_id
        config = load_config()
        chat_id = config['platforms'][platform_id]['chat_id']
        thread_id = None
        if topic:
            thread_id = config['platforms'][platform_id]['topics'][topic].get('thread_id')
        
        try:
            # Отправляем как текстовое сообщение
            if thread_id:
                await callback.bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode='HTML',
                    disable_web_page_preview=False,
                    message_thread_id=thread_id
                )
            else:
                await callback.bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode='HTML',
                    disable_web_page_preview=False
                )
            
            await callback.message.edit_text("✅ Комикс опубликован!")
            await callback.message.answer(
                "Что дальше?",
                reply_markup=after_post_keyboard()
            )
            await state.clear()
            
        except Exception as e:
            logger.error(f"Ошибка публикации комикса: {e}")
            await callback.message.edit_text(f"❌ Ошибка публикации: {str(e)}")
        
        return
    
    if action == "add_hashtags":
        data = await state.get_data()
        category_data = data['category_data']
        cat_key = data['category_key']
        all_hashtags = category_data.get('hashtags', [])
        selected = data.get('selected_hashtags', [])
        
        await callback.message.edit_text(
            "🏷 <b>Выберите хештеги для комикса:</b>\n\n"
            "💡 Для комиксов хештеги необязательны",
            reply_markup=hashtags_keyboard(
                all_hashtags, selected,
                callback.from_user.id, cat_key
            ),
            parse_mode='HTML'
        )
        await state.set_state(PostFlow.comic_hashtags)
        return
    
    if action == "edit_title":
        await callback.message.answer(
            "📝 <b>Введите новое название комикса:</b>",
            reply_markup=comic_input_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(PostFlow.comic_title)
        return
    
    if action == "edit_link":
        await callback.message.answer(
            "🔗 <b>Введите новую ссылку на комикс:</b>",
            reply_markup=comic_input_keyboard(),
            parse_mode='HTML'
        )
        await state.set_state(PostFlow.comic_link)
        return
    
    if action == "cancel":
        await callback.message.edit_text("❌ Отменено")
        await callback.message.answer("Что дальше?", reply_markup=main_menu())
        await state.clear()
        return


# ============ ЗАГРУЗКА МЕДИА (АНТИСПАМ!) ============

async def handle_media_input(message: Message, state: FSMContext):
    """Обработка входящих медиа с антиспамом"""
    user_id = message.from_user.id
    
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    
    async with user_locks[user_id]:
        data = await state.get_data()
        media_files = data.get('media_files', [])
        
        # Добавляем медиа
        if message.photo:
            file_id = message.photo[-1].file_id
            unique_id = message.photo[-1].file_unique_id
            media_files.append(('photo', file_id, unique_id))
        elif message.video:
            file_id = message.video.file_id
            unique_id = message.video.file_unique_id
            media_files.append(('video', file_id, unique_id))
        
        # Дебаунс для альбомов
        if message.media_group_id:
            import time
            ts = time.time()
            await state.update_data(media_files=media_files, last_media_ts=ts)
            await asyncio.sleep(0.5)
            
            # Проверяем что мы последние
            data2 = await state.get_data()
            if data2.get('last_media_ts') != ts:
                return
        else:
            await state.update_data(media_files=media_files)
        
        # Обновляем статус ОДНИМ сообщением
        count = len(media_files)
        await safe_edit_status(
            message, state,
            f"✅ Загружено: <b>{count}</b> файл(ов)\n\n"
            "Отправьте еще или нажмите <b>➡️ Далее</b>"
        )

@router.message(PostFlow.uploading_media, F.photo | F.video)
@router.message(PostFlow.batch_uploading, F.photo | F.video)
async def media_received(message: Message, state: FSMContext):
    """Получение медиа"""
    await handle_media_input(message, state)

# ============ КНОПКИ УПРАВЛЕНИЯ ЗАГРУЗКОЙ ============

@router.message(PostFlow.uploading_media, F.text.startswith("➡️ Далее"))
@router.message(PostFlow.batch_uploading, F.text.startswith("➡️ Далее"))
async def media_done(message: Message, state: FSMContext):
    """Завершение загрузки медиа"""
    data = await state.get_data()
    media_files = data.get('media_files', [])
    
    if not media_files:
        await message.answer("❌ Сначала отправьте хотя бы один файл")
        return
    
    is_batch = await state.get_state() == PostFlow.batch_uploading
    
    if is_batch:
        # Пакетный режим - спрашиваем сколько файлов в посте
        await state.set_state(PostFlow.batch_schedule)
        await state.update_data(batch_step='files_per_post')
        await message.answer(
            f"📊 Загружено <b>{len(media_files)}</b> файлов\n\n"
            "⚙️ Сколько файлов объединять в один пост?",
            reply_markup=files_per_post_keyboard(),
            parse_mode='HTML'
        )
    else:
        # Обычный режим - переходим к хештегам
        category_data = data['category_data']
        all_hashtags = category_data.get('hashtags', [])
        cat_key = data['category_key']
        
        await state.set_state(PostFlow.selecting_hashtags)
        await message.answer(
            "🏷 <b>Выберите хештеги:</b>",
            reply_markup=hashtags_keyboard(
                all_hashtags, [],
                message.from_user.id, cat_key
            ),
            parse_mode='HTML'
        )

@router.message(PostFlow.uploading_media, F.text == "🗑️ Очистить всё")
@router.message(PostFlow.batch_uploading, F.text == "🗑️ Очистить всё")
async def clear_media(message: Message, state: FSMContext):
    """Очистка загруженных файлов"""
    await state.update_data(media_files=[])
    await message.answer(
        "🗑️ Файлы очищены\n\nОтправьте новые фото или видео:",
        reply_markup=media_upload_keyboard(0)
    )

@router.message(PostFlow.uploading_media, F.text == "❌ Отмена")
@router.message(PostFlow.batch_uploading, F.text == "❌ Отмена")
async def cancel_upload(message: Message, state: FSMContext):
    """Отмена загрузки"""
    await cmd_start(message, state)

# ============ СОХРАНЕНИЕ В ЧЕРНОВИК/ОЧЕРЕДЬ ============

@router.message(PostFlow.uploading_media, F.text == "💾 В черновик")
@router.message(PostFlow.batch_uploading, F.text == "💾 В черновик")
async def save_to_draft_prompt(message: Message, state: FSMContext):
    """Запрос названия для черновика"""
    data = await state.get_data()
    media_files = data.get('media_files', [])
    
    if not media_files:
        await message.answer("❌ Сначала загрузите файлы")
        return
    
    await state.update_data(saving_to='draft')
    await message.answer(
        "📝 <b>Введите название черновика:</b>\n"
        "(Например: Альтушки 10.02 15:30)",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        ),
        parse_mode='HTML'
    )

@router.message(PostFlow.uploading_media, F.text == "📥 В очередь")
@router.message(PostFlow.batch_uploading, F.text == "📥 В очередь")
async def save_to_queue_prompt(message: Message, state: FSMContext):
    """Запрос названия для очереди"""
    data = await state.get_data()
    media_files = data.get('media_files', [])
    
    if not media_files:
        await message.answer("❌ Сначала загрузите файлы")
        return
    
    # Сразу переходим к выбору хештегов
    category_data = data['category_data']
    all_hashtags = category_data.get('hashtags', [])
    cat_key = data['category_key']
    
    await state.update_data(saving_to='queue')
    await state.set_state(PostFlow.selecting_hashtags)
    await message.answer(
        "📥 <b>Добавление в очередь</b>\n\n"
        "🏷 Выберите хештеги:",
        reply_markup=hashtags_keyboard(
            all_hashtags, [],
            message.from_user.id, cat_key
        ),
        parse_mode='HTML'
    )

@router.message(PostFlow.uploading_media)
@router.message(PostFlow.batch_uploading)
async def save_named_item(message: Message, state: FSMContext):
    """Сохранение черновика/очереди с названием"""
    data = await state.get_data()
    saving_to = data.get('saving_to')
    
    if not saving_to:
        return
    
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return
    
    name = message.text
    
    if saving_to == 'draft':
        # Сохраняем в черновики
        draft_id = draft_mgr.create_draft(
            user_id=message.from_user.id,
            name=name,
            category_key=data['category_key'],
            category_data=data['category_data'],
            platform_id=data['platform_id'],
            media_files=data.get('media_files', []),
            selected_hashtags=[],
            description="",
            topic=data.get('topic')
        )
        
        await message.answer(
            f"💾 Сохранено в черновики!\n\n"
            f"📄 Название: <b>{name}</b>\n"
            f"🆔 ID: <code>{draft_id}</code>",
            reply_markup=main_menu(),
            parse_mode='HTML'
        )
        await state.clear()


# ============ ХЕШТЕГИ - ОСНОВНОЙ ФУНКЦИОНАЛ ============

@router.callback_query(F.data.startswith("tag:"))
async def hashtag_selection(callback: CallbackQuery, state: FSMContext):
    """Выбор хештегов"""
    data = await state.get_data()
    category_data = data['category_data']
    all_hashtags = category_data.get('hashtags', [])
    selected = data.get('selected_hashtags', [])
    cat_key = data['category_key']
    
    action = callback.data[4:]  # убираем "tag:"
    
    if action == "select_all":
        selected = all_hashtags.copy()
        await state.update_data(selected_hashtags=selected)
        await callback.answer("✅ Все выбраны")
        
        await callback.message.edit_reply_markup(
            reply_markup=hashtags_keyboard(
                all_hashtags, selected,
                callback.from_user.id, cat_key
            )
        )
        return
    
    if action == "clear_all":
        selected = []
        await state.update_data(selected_hashtags=selected)
        await callback.answer("❌ Сброшено")
        
        await callback.message.edit_reply_markup(
            reply_markup=hashtags_keyboard(
                all_hashtags, selected,
                callback.from_user.id, cat_key
            )
        )
        return
    
    if action == "templates":
        # Показываем шаблоны
        await callback.message.edit_text(
            "📋 <b>Мои шаблоны хештегов:</b>\n\n"
            "Выберите шаблон для применения:",
            reply_markup=hashtag_templates_keyboard(
                callback.from_user.id, cat_key, selected
            ),
            parse_mode='HTML'
        )
        return
    
    if action == "back":
        # Возврат к хештегам
        await callback.message.edit_text(
            "🏷 <b>Выберите хештеги:</b>",
            reply_markup=hashtags_keyboard(
                all_hashtags, selected,
                callback.from_user.id, cat_key
            ),
            parse_mode='HTML'
        )
        return
    
    if action == "add_custom":
        # Добавление пользовательского хештега
        await callback.message.answer(
            "➕ <b>Добавление своего хештега</b>\n\n"
            "Введите хештег (например: #myhashtag):\n\n"
            "💡 Хештег должен начинаться с # и не содержать пробелов",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            ),
            parse_mode='HTML'
        )
        await state.set_state(HashtagFlow.adding_custom)
        return
    
    if action == "save_template":
        # Сохранение как шаблон
        if not selected:
            await callback.answer("❌ Выберите хотя бы один хештег", show_alert=True)
            return
        
        await callback.message.answer(
            "💾 <b>Сохранение шаблона</b>\n\n"
            f"Выбрано хештегов: {len(selected)}\n\n"
            "Введите название шаблона:\n"
            "(Например: Топ альтушки, Популярные, Минимум)",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            ),
            parse_mode='HTML'
        )
        await state.set_state(HashtagFlow.naming_template)
        return
    
    if action == "done":
        # Завершение выбора хештегов
        current_state = await state.get_state()
        saving_to = data.get('saving_to')
        is_comic = data.get('is_comic_mode', False)
        
        # Для комиксов - возвращаемся к превью
        if current_state == PostFlow.comic_hashtags or is_comic:
            await state.update_data(selected_hashtags=selected)
            comic_link = data.get('comic_link', '')
            comic_title = data.get('comic_title', '')
            platform_id = data['platform_id']
            
            preview = format_comic_post(
                category_data=category_data,
                comic_title=comic_title,
                comic_link=comic_link,
                selected_hashtags=selected,
                platform_id=platform_id
            )
            
            await callback.message.edit_text("📋 <b>Обновленное превью:</b>", parse_mode='HTML')
            await callback.message.answer(preview, parse_mode='HTML', disable_web_page_preview=True)
            await callback.message.answer(
                "Что дальше?",
                reply_markup=comic_confirm_keyboard()
            )
            return
        
        # Для очереди - запрашиваем название
        if saving_to == 'queue':
            await state.update_data(selected_hashtags=selected)
            await callback.message.edit_text(
                f"✅ Выбрано хештегов: {len(selected)}\n\n"
                "📝 Введите название для очереди:"
            )
            await callback.message.answer(
                "Введите название:",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="❌ Отмена")]],
                    resize_keyboard=True
                )
            )
            return
        
        # Обычная публикация
        await state.update_data(selected_hashtags=selected)
        await publish_post(callback.message, state, callback.from_user.id)
        return
    
    # Переключение конкретного хештега
    if action in all_hashtags:
        if action in selected:
            selected.remove(action)
        else:
            selected.append(action)
        
        await state.update_data(selected_hashtags=selected)
        
        await callback.message.edit_reply_markup(
            reply_markup=hashtags_keyboard(
                all_hashtags, selected,
                callback.from_user.id, cat_key
            )
        )
        return

# ============ ПРИМЕНЕНИЕ ШАБЛОНОВ ХЕШТЕГОВ ============

@router.callback_query(F.data.startswith("tmpl:"))
async def apply_hashtag_template(callback: CallbackQuery, state: FSMContext):
    """Применение шаблона хештегов"""
    parts = callback.data.split(":")
    action = parts[1]
    
    if action == "apply":
        template_name = parts[2]
        
        data = await state.get_data()
        cat_key = data['category_key']
        
        config = load_config()
        user_str = str(callback.from_user.id)
        template_tags = config.get('hashtag_templates', {}).get(user_str, {}).get(cat_key, {}).get(template_name, [])
        
        if not template_tags:
            await callback.answer("❌ Шаблон не найден", show_alert=True)
            return
        
        # Применяем шаблон
        selected = data.get('selected_hashtags', [])
        
        # Проверяем, активен ли уже этот шаблон
        is_active = all(tag in selected for tag in template_tags)
        
        if is_active:
            # Если активен - убираем эти теги
            selected = [tag for tag in selected if tag not in template_tags]
            await callback.answer(f"❌ Шаблон \"{template_name}\" снят")
        else:
            # Добавляем теги из шаблона
            for tag in template_tags:
                if tag not in selected:
                    selected.append(tag)
            await callback.answer(f"✅ Шаблон \"{template_name}\" применен")
        
        await state.update_data(selected_hashtags=selected)
        
        # Обновляем клавиатуру
        await callback.message.edit_reply_markup(
            reply_markup=hashtag_templates_keyboard(
                callback.from_user.id, cat_key, selected
            )
        )
        return

# ============ ДОБАВЛЕНИЕ ПОЛЬЗОВАТЕЛЬСКИХ ХЕШТЕГОВ ============

@router.message(HashtagFlow.adding_custom)
async def custom_hashtag_received(message: Message, state: FSMContext):
    """Получен пользовательский хештег"""
    if message.text == "❌ Отмена":
        data = await state.get_data()
        category_data = data['category_data']
        all_hashtags = category_data.get('hashtags', [])
        selected = data.get('selected_hashtags', [])
        cat_key = data['category_key']
        
        await state.set_state(PostFlow.selecting_hashtags)
        await message.answer(
            "🏷 <b>Выберите хештеги:</b>",
            reply_markup=hashtags_keyboard(
                all_hashtags, selected,
                message.from_user.id, cat_key
            ),
            parse_mode='HTML'
        )
        return
    
    # Валидация хештега
    hashtag = message.text.strip()
    
    if not hashtag.startswith('#'):
        await message.answer(
            "❌ Хештег должен начинаться с #\n\n"
            "Попробуйте еще раз:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            )
        )
        return
    
    if ' ' in hashtag:
        await message.answer(
            "❌ Хештег не должен содержать пробелов\n\n"
            "Попробуйте еще раз:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            )
        )
        return
    
    # Добавляем хештег в категорию
    data = await state.get_data()
    category_data = data['category_data']
    cat_key = data['category_key']
    
    config = load_config()
    
    # Находим и обновляем категорию
    for participant_key, participant_data in config['participants'].items():
        if participant_data.get('user_id') == message.from_user.id:
            if cat_key in participant_data['categories']:
                if hashtag not in participant_data['categories'][cat_key]['hashtags']:
                    participant_data['categories'][cat_key]['hashtags'].append(hashtag)
                    save_config(config)
                    
                    # Обновляем в state
                    category_data['hashtags'].append(hashtag)
                    await state.update_data(category_data=category_data)
                    
                    # Автоматически выбираем новый хештег
                    selected = data.get('selected_hashtags', [])
                    selected.append(hashtag)
                    await state.update_data(selected_hashtags=selected)
                    
                    await message.answer(
                        f"✅ Хештег <b>{hashtag}</b> добавлен и выбран!",
                        parse_mode='HTML'
                    )
                else:
                    # Хештег уже есть - просто выбираем его
                    selected = data.get('selected_hashtags', [])
                    if hashtag not in selected:
                        selected.append(hashtag)
                        await state.update_data(selected_hashtags=selected)
                    
                    await message.answer(
                        f"ℹ️ Хештег <b>{hashtag}</b> уже существует и выбран!",
                        parse_mode='HTML'
                    )
                
                break
    
    # Возвращаемся к выбору хештегов
    all_hashtags = category_data.get('hashtags', [])
    selected = data.get('selected_hashtags', [])
    
    await state.set_state(PostFlow.selecting_hashtags)
    await message.answer(
        "🏷 <b>Выберите хештеги:</b>",
        reply_markup=hashtags_keyboard(
            all_hashtags, selected,
            message.from_user.id, cat_key
        ),
        parse_mode='HTML'
    )

# ============ СОХРАНЕНИЕ ШАБЛОНОВ ХЕШТЕГОВ ============

@router.message(HashtagFlow.naming_template)
async def save_hashtag_template(message: Message, state: FSMContext):
    """Сохранение шаблона хештегов"""
    if message.text == "❌ Отмена":
        data = await state.get_data()
        category_data = data['category_data']
        all_hashtags = category_data.get('hashtags', [])
        selected = data.get('selected_hashtags', [])
        cat_key = data['category_key']
        
        await state.set_state(PostFlow.selecting_hashtags)
        await message.answer(
            "🏷 <b>Выберите хештеги:</b>",
            reply_markup=hashtags_keyboard(
                all_hashtags, selected,
                message.from_user.id, cat_key
            ),
            parse_mode='HTML'
        )
        return
    
    template_name = message.text.strip()
    data = await state.get_data()
    selected = data.get('selected_hashtags', [])
    cat_key = data['category_key']
    
    # Сохраняем шаблон в config
    config = load_config()
    user_str = str(message.from_user.id)
    
    if 'hashtag_templates' not in config:
        config['hashtag_templates'] = {}
    
    if user_str not in config['hashtag_templates']:
        config['hashtag_templates'][user_str] = {}
    
    if cat_key not in config['hashtag_templates'][user_str]:
        config['hashtag_templates'][user_str][cat_key] = {}
    
    config['hashtag_templates'][user_str][cat_key][template_name] = selected
    save_config(config)
    
    await message.answer(
        f"💾 Шаблон <b>\"{template_name}\"</b> сохранен!\n\n"
        f"📊 Хештегов в шаблоне: {len(selected)}",
        parse_mode='HTML'
    )
    
    # Возвращаемся к выбору хештегов
    category_data = data['category_data']
    all_hashtags = category_data.get('hashtags', [])
    
    await state.set_state(PostFlow.selecting_hashtags)
    await message.answer(
        "🏷 <b>Выберите хештеги:</b>",
        reply_markup=hashtags_keyboard(
            all_hashtags, selected,
            message.from_user.id, cat_key, show_templates=True
        ),
        parse_mode='HTML'
    )


# ============ ПУБЛИКАЦИЯ ПОСТА ============

async def publish_post(message: Message, state: FSMContext, user_id: int):
    """Публикация готового поста"""
    data = await state.get_data()
    
    media_files = data.get('media_files', [])
    selected_hashtags = data.get('selected_hashtags', [])
    category_data = data['category_data']
    platform_id = data['platform_id']
    topic = data.get('topic')
    description = data.get('description', '')
    
    if not media_files:
        await message.answer("❌ Нет файлов для публикации")
        return
    
    # Обрабатываем медиа
    processor = MediaProcessor(message.bot)
    
    await message.edit_text("⏳ Обработка файлов...")
    
    # Преобразуем media_files в нужный формат
    media_to_process = [(mtype, fid) for mtype, fid, _ in media_files]
    processed_media = await processor.process_batch(media_to_process)
    
    if not processed_media:
        await message.edit_text("❌ Ошибка обработки файлов")
        return
    
    # Формируем caption
    caption = format_post_caption(category_data, selected_hashtags, platform_id, description)
    
    # Получаем chat_id
    config = load_config()
    chat_id = config['platforms'][platform_id]['chat_id']
    thread_id = None
    if topic:
        thread_id = config['platforms'][platform_id]['topics'][topic].get('thread_id')
    
    # Публикуем
    await message.edit_text("📤 Публикация...")
    
    try:
        if len(processed_media) == 1:
            success = await processor.send_single_media(
                chat_id, processed_media[0][0], processed_media[0][1], caption, thread_id
            )
        else:
            success = await processor.send_media_group(
                chat_id, processed_media, caption, thread_id
            )
        
        # Очищаем файлы
        paths = [p for _, p in processed_media]
        processor.cleanup_specific(paths)
        
        if success:
            await message.edit_text("✅ Пост успешно опубликован!")
            await message.answer("Что дальше?", reply_markup=after_post_keyboard())
            await state.clear()
        else:
            await message.edit_text("❌ Ошибка публикации")
    
    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        await message.edit_text(f"❌ Ошибка: {str(e)}")

# ============ СОХРАНЕНИЕ В ОЧЕРЕДЬ С НАЗВАНИЕМ ============

@router.message(PostFlow.selecting_hashtags)
async def save_to_queue_with_name(message: Message, state: FSMContext):
    """Сохранение в очередь после выбора хештегов"""
    data = await state.get_data()
    saving_to = data.get('saving_to')
    
    if saving_to != 'queue':
        return
    
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return
    
    name = message.text
    
    queue_mgr.add_to_queue(
        user_id=message.from_user.id,
        name=name,
        category_key=data['category_key'],
        category_data=data['category_data'],
        platform_id=data['platform_id'],
        media_files=data.get('media_files', []),
        selected_hashtags=data.get('selected_hashtags', []),
        description=data.get('description', ''),
        topic=data.get('topic')
    )
    
    await message.answer(
        f"📥 Добавлено в очередь!\n\n"
        f"📄 Название: <b>{name}</b>\n"
        f"📊 Хештегов: {len(data.get('selected_hashtags', []))}",
        reply_markup=main_menu(),
        parse_mode='HTML'
    )
    await state.clear()

# ============ ПАКЕТНАЯ ЗАГРУЗКА ============

@router.message(F.text == "📦 Пакетная загрузка")
async def batch_upload_start(message: Message, state: FSMContext):
    """Начало пакетной загрузки"""
    await state.clear()
    await state.set_state(PostFlow.selecting_category)
    await state.update_data(is_batch_mode=True)
    await message.answer(
        "📦 <b>Пакетная загрузка</b>\n\n"
        "📁 Выберите категорию:",
        reply_markup=categories_keyboard(message.from_user.id),
        parse_mode='HTML'
    )

@router.callback_query(F.data.startswith("files_per_post:"))
async def batch_files_per_post_selected(callback: CallbackQuery, state: FSMContext):
    """Выбрано количество файлов в посте"""
    files_per_post = int(callback.data.split(":")[1])
    
    data = await state.get_data()
    media_files = data.get('media_files', [])
    posts_count = len(media_files) // files_per_post + (1 if len(media_files) % files_per_post else 0)
    
    await state.update_data(
        files_per_post=files_per_post,
        batch_step='time_category'
    )
    
    await callback.message.edit_text(
        f"✅ Файлов в посте: <b>{files_per_post}</b>\n"
        f"📊 Получится постов: <b>{posts_count}</b>\n\n"
        "⏰ За какой период публиковать?",
        reply_markup=batch_schedule_keyboard(),
        parse_mode='HTML'
    )

@router.callback_query(F.data.startswith("time:category:"))
async def batch_time_category(callback: CallbackQuery, state: FSMContext):
    """Выбрана единица времени"""
    time_cat = callback.data.split(":")[-1]
    
    await state.update_data(
        time_category=time_cat,
        batch_step='time_value'
    )
    
    await callback.message.edit_text(
        "⏱ Выберите длительность:",
        reply_markup=time_options_keyboard(time_cat)
    )

@router.callback_query(F.data == "time:back")
async def batch_time_back(callback: CallbackQuery, state: FSMContext):
    """Назад к выбору единицы времени"""
    await state.update_data(batch_step='time_category')
    
    await callback.message.edit_text(
        "⏰ За какой период публиковать?",
        reply_markup=batch_schedule_keyboard()
    )

@router.callback_query(F.data.startswith("schedule:"))
async def batch_schedule_selected(callback: CallbackQuery, state: FSMContext):
    """Выбрано расписание"""
    parts = callback.data.split(":")
    
    if parts[1] == "cancel":
        await callback.message.edit_text("❌ Отменено")
        await callback.message.answer("Что дальше?", reply_markup=main_menu())
        await state.clear()
        return
    
    time_category = parts[1]
    time_value = int(parts[2])
    
    data = await state.get_data()
    media_files = data.get('media_files', [])
    files_per_post = data.get('files_per_post', 1)
    
    # Разбиваем на посты
    posts = []
    for i in range(0, len(media_files), files_per_post):
        posts.append(media_files[i:i + files_per_post])
    
    posts_count = len(posts)
    
    # Создаем расписание
    batch_service = BatchPostingService(MediaProcessor(callback.message.bot), callback.message.bot)
    schedule = batch_service.generate_schedule(posts_count, time_category, time_value)
    
    # Сохраняем данные
    await state.update_data(
        time_category=time_category,
        time_value=time_value,
        schedule=schedule,
        posts=posts,
        batch_step='confirm'
    )
    
    # Показываем summary
    summary = batch_service.get_batch_summary(posts_count, schedule, time_category, time_value)
    
    await callback.message.edit_text(
        summary,
        reply_markup=confirm_batch_final_keyboard(posts_count),
        parse_mode='HTML'
    )
    
    # Переходим к выбору хештегов
    category_data = data['category_data']
    all_hashtags = category_data.get('hashtags', [])
    cat_key = data['category_key']
    
    await callback.message.answer(
        "🏷 <b>Выберите хештеги для всех постов:</b>",
        reply_markup=hashtags_keyboard(
            all_hashtags, [],
            callback.from_user.id, cat_key
        ),
        parse_mode='HTML'
    )
    await state.set_state(PostFlow.selecting_hashtags)

@router.callback_query(F.data == "batch:confirm_publish")
async def batch_publish_confirmed(callback: CallbackQuery, state: FSMContext):
    """Подтверждение публикации пакета"""
    data = await state.get_data()
    
    posts = data.get('posts', [])
    category_data = data['category_data']
    platform_id = data['platform_id']
    selected_hashtags = data.get('selected_hashtags', [])
    schedule = data.get('schedule', [])
    topic = data.get('topic')
    
    if not posts or not schedule:
        await callback.answer("❌ Ошибка данных", show_alert=True)
        return
    
    await callback.message.edit_text("⏳ Запуск пакетной публикации...")
    
    # Запускаем пакетную публикацию
    batch_service = BatchPostingService(MediaProcessor(callback.message.bot), callback.message.bot)
    
    # Преобразуем посты в нужный формат
    posts_formatted = []
    for post in posts:
        post_media = [(mtype, fid) for mtype, fid, _ in post]
        posts_formatted.append(post_media)
    
    scheduled_count = await batch_service.schedule_batch(
        posts=posts_formatted,
        category_data=category_data,
        platform_id=platform_id,
        hashtags=selected_hashtags,
        schedule=schedule,
        thread_id=topic
    )
    
    await callback.message.edit_text(
        f"✅ Пакетная публикация запущена!\n\n"
        f"📊 Запланировано постов: <b>{scheduled_count}</b>\n"
        f"🕐 Первый пост опубликован сейчас\n"
        f"🕐 Последний пост: <b>{schedule[-1].strftime('%d.%m %H:%M')}</b>",
        parse_mode='HTML'
    )
    
    await callback.message.answer("Что дальше?", reply_markup=after_post_keyboard())
    await state.clear()


# ============ УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЬСКИМИ ХЕШТЕГАМИ ============

@router.message(F.text == "#️⃣ Мои хештеги")
async def my_hashtags_menu(message: Message, state: FSMContext):
    """Меню управления хештегами"""
    await message.answer(
        "#️⃣ <b>Управление хештегами</b>\n\n"
        "Здесь вы можете:\n"
        "• Просматривать сохраненные шаблоны\n"
        "• Управлять своими хештегами\n"
        "• Удалять ненужные шаблоны",
        reply_markup=my_hashtags_menu_keyboard(message.from_user.id),
        parse_mode='HTML'
    )

@router.callback_query(F.data.startswith("myhash:"))
async def my_hashtags_actions(callback: CallbackQuery, state: FSMContext):
    """Действия в меню хештегов"""
    parts = callback.data.split(":")
    action = parts[1]
    
    if action == "back":
        await callback.message.edit_text("👋 Главное меню")
        await callback.message.answer("Выберите действие:", reply_markup=main_menu())
        return
    
    if action == "menu":
        await callback.message.edit_text(
            "#️⃣ <b>Управление хештегами</b>\n\n"
            "Здесь вы можете:\n"
            "• Просматривать сохраненные шаблоны\n"
            "• Управлять своими хештегами\n"
            "• Удалять ненужные шаблоны",
            reply_markup=my_hashtags_menu_keyboard(callback.from_user.id),
            parse_mode='HTML'
        )
        return
    
    if action == "help":
        help_text = (
            "ℹ️ <b>Как использовать хештеги</b>\n\n"
            "<b>📋 Шаблоны хештегов:</b>\n"
            "Это сохраненные наборы хештегов для быстрого применения.\n\n"
            "<b>Как создать шаблон:</b>\n"
            "1. При выборе хештегов нажмите '💾 Сохранить как шаблон'\n"
            "2. Введите название\n"
            "3. Шаблон сохранен!\n\n"
            "<b>Как использовать:</b>\n"
            "При выборе хештегов нажмите '📋 Мои шаблоны' и выберите нужный.\n\n"
            "<b>➕ Свои хештеги:</b>\n"
            "При выборе хештегов нажмите '➕ Свой хештег' и введите его.\n"
            "Хештег сохранится в категории навсегда!"
        )
        await callback.message.edit_text(
            help_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="myhash:menu")]
            ]),
            parse_mode='HTML'
        )
        return
    
    if action == "templates":
        await callback.message.edit_text(
            "📋 <b>Мои шаблоны хештегов</b>\n\n"
            "Выберите категорию:",
            reply_markup=user_templates_list_keyboard(callback.from_user.id),
            parse_mode='HTML'
        )
        return
    
    if action == "cat":
        category_key = parts[2]
        await callback.message.edit_text(
            "📋 <b>Шаблоны категории</b>\n\n"
            "Выберите шаблон:",
            reply_markup=category_templates_keyboard(callback.from_user.id, category_key),
            parse_mode='HTML'
        )
        return
    
    if action == "view":
        category_key = parts[2]
        template_name = ":".join(parts[3:])  # На случай если в названии есть двоеточия
        
        config = load_config()
        user_str = str(callback.from_user.id)
        template_tags = config.get('hashtag_templates', {}).get(user_str, {}).get(category_key, {}).get(template_name, [])
        
        tags_text = '\n'.join(template_tags)
        
        await callback.message.edit_text(
            f"📋 <b>Шаблон: {template_name}</b>\n\n"
            f"📊 Хештегов: {len(template_tags)}\n\n"
            f"{tags_text}",
            reply_markup=template_actions_keyboard(category_key, template_name),
            parse_mode='HTML'
        )
        return
    
    if action == "delete":
        category_key = parts[2]
        template_name = ":".join(parts[3:])
        
        config = load_config()
        user_str = str(callback.from_user.id)
        
        if user_str in config.get('hashtag_templates', {}):
            if category_key in config['hashtag_templates'][user_str]:
                if template_name in config['hashtag_templates'][user_str][category_key]:
                    del config['hashtag_templates'][user_str][category_key][template_name]
                    save_config(config)
                    
                    await callback.answer("🗑️ Шаблон удален")
                    
                    # Возвращаемся к списку шаблонов категории
                    await callback.message.edit_text(
                        "📋 <b>Шаблоны категории</b>\n\n"
                        "Выберите шаблон:",
                        reply_markup=category_templates_keyboard(callback.from_user.id, category_key),
                        parse_mode='HTML'
                    )
                    return
        
        await callback.answer("❌ Шаблон не найден", show_alert=True)
        return

# ============ ЧЕРНОВИКИ ============

@router.message(F.text == "💾 Черновики")
async def show_drafts(message: Message, state: FSMContext):
    """Показать черновики"""
    user_drafts = draft_mgr.get_user_drafts(message.from_user.id)
    
    if not user_drafts:
        await message.answer(
            "📂 <b>Черновики пусты</b>\n\n"
            "Черновики создаются через кнопку '💾 В черновик' при загрузке медиа.",
            reply_markup=main_menu(),
            parse_mode='HTML'
        )
        return
    
    await message.answer(
        f"📂 <b>Ваши черновики ({len(user_drafts)})</b>\n\n"
        "Выберите черновик:",
        reply_markup=drafts_list_keyboard(user_drafts),
        parse_mode='HTML'
    )

@router.callback_query(F.data.startswith("dr:"))
async def draft_actions(callback: CallbackQuery, state: FSMContext):
    """Действия с черновиками"""
    parts = callback.data.split(":")
    action = parts[1]
    
    if action == "back":
        await callback.message.edit_text("👋 Главное меню")
        await callback.message.answer("Выберите действие:", reply_markup=main_menu())
        return
    
    if action == "list":
        user_drafts = draft_mgr.get_user_drafts(callback.from_user.id)
        if user_drafts:
            await callback.message.edit_text(
                f"📂 <b>Ваши черновики ({len(user_drafts)})</b>\n\n"
                "Выберите черновик:",
                reply_markup=drafts_list_keyboard(user_drafts),
                parse_mode='HTML'
            )
        else:
            await callback.message.edit_text("📂 Черновики пусты")
            await callback.message.answer("Что дальше?", reply_markup=main_menu())
        return
    
    if action == "open":
        draft_id = parts[2]
        draft = draft_mgr.get_draft(draft_id)
        
        if not draft:
            await callback.answer("❌ Черновик не найден", show_alert=True)
            return
        
        info = draft_mgr.get_draft_info(draft_id)
        
        await callback.message.edit_text(
            info,
            reply_markup=draft_action_keyboard(draft_id),
            parse_mode='HTML'
        )
        return
    
    if action == "pub":
        draft_id = parts[2]
        draft = draft_mgr.get_draft(draft_id)
        
        if not draft:
            await callback.answer("❌ Черновик не найден", show_alert=True)
            return
        
        await callback.answer("📤 Публикуем...")
        
        # Проверяем, это комикс или обычный пост
        is_comic = draft.get('is_comic', False)
        
        if is_comic:
            # Публикация комикса
            caption = format_comic_post(
                draft['category_data'],
                draft.get('comic_title', ''),
                draft.get('comic_link', ''),
                draft['selected_hashtags'],
                draft['platform_id']
            )
            
            config = load_config()
            chat_id = config['platforms'][draft['platform_id']]['chat_id']
            thread_id = None
            if draft.get('topic'):
                thread_id = config['platforms'][draft['platform_id']]['topics'][draft['topic']].get('thread_id')
            
            try:
                if thread_id:
                    await callback.bot.send_message(
                        chat_id=chat_id,
                        text=caption,
                        parse_mode='HTML',
                        disable_web_page_preview=False,
                        message_thread_id=thread_id
                    )
                else:
                    await callback.bot.send_message(
                        chat_id=chat_id,
                        text=caption,
                        parse_mode='HTML',
                        disable_web_page_preview=False
                    )
                
                draft_mgr.delete_draft(draft_id)
                await callback.message.edit_text("✅ Комикс опубликован и удален из черновиков!")
                await callback.message.answer("Что дальше?", reply_markup=main_menu())
            except Exception as e:
                logger.error(f"Ошибка публикации комикса из черновика: {e}")
                await callback.message.edit_text(f"❌ Ошибка публикации: {str(e)}")
            return
        
        # Публикация обычного поста
        processor = MediaProcessor(callback.message.bot)
        
        media_to_process = [(mtype, fid) for mtype, fid, _ in draft['media_files']]
        processed_media = await processor.process_batch(media_to_process)
        
        if not processed_media:
            await callback.message.edit_text("❌ Ошибка обработки файлов")
            return
        
        config = load_config()
        chat_id = config['platforms'][draft['platform_id']]['chat_id']
        thread_id = None
        if draft.get('topic'):
            thread_id = config['platforms'][draft['platform_id']]['topics'][draft['topic']].get('thread_id')
        
        caption = format_post_caption(
            draft['category_data'],
            draft['selected_hashtags'],
            draft['platform_id'],
            draft.get('description', '')
        )
        
        success = False
        if len(processed_media) == 1:
            success = await processor.send_single_media(
                chat_id, processed_media[0][0], processed_media[0][1], caption, thread_id
            )
        else:
            success = await processor.send_media_group(
                chat_id, processed_media, caption, thread_id
            )
        
        paths = [p for _, p in processed_media]
        processor.cleanup_specific(paths)
        
        if success:
            draft_mgr.delete_draft(draft_id)
            await callback.message.edit_text("✅ Черновик опубликован и удален!")
            await callback.message.answer("Что дальше?", reply_markup=main_menu())
        else:
            await callback.message.edit_text("❌ Ошибка публикации")
        return
    
    if action == "toq":
        draft_id = parts[2]
        draft = draft_mgr.get_draft(draft_id)
        
        if not draft:
            await callback.answer("❌ Черновик не найден", show_alert=True)
            return
        
        queue_mgr.add_to_queue(
            user_id=callback.from_user.id,
            name=draft['name'],
            category_key=draft['category_key'],
            category_data=draft['category_data'],
            platform_id=draft['platform_id'],
            media_files=draft['media_files'],
            selected_hashtags=draft['selected_hashtags'],
            description=draft.get('description', ''),
            topic=draft.get('topic'),
            is_comic=draft.get('is_comic', False),
            comic_link=draft.get('comic_link', ''),
            comic_title=draft.get('comic_title', '')
        )
        
        draft_mgr.delete_draft(draft_id)
        await callback.answer("✅ Добавлено в очередь")
        await callback.message.edit_text("✅ Черновик перемещен в очередь!")
        await callback.message.answer("Что дальше?", reply_markup=main_menu())
        return
    
    if action == "del":
        draft_id = parts[2]
        draft_mgr.delete_draft(draft_id)
        await callback.answer("🗑️ Удалено")
        
        user_drafts = draft_mgr.get_user_drafts(callback.from_user.id)
        if user_drafts:
            await callback.message.edit_text(
                f"📂 <b>Ваши черновики ({len(user_drafts)})</b>\n\nВыберите черновик:",
                reply_markup=drafts_list_keyboard(user_drafts),
                parse_mode='HTML'
            )
        else:
            await callback.message.edit_text("📂 Черновики пусты")
            await callback.message.answer("Что дальше?", reply_markup=main_menu())
        return


# ============ ОЧЕРЕДЬ ============

@router.message(F.text == "📥 Очередь")
async def show_queue(message: Message, state: FSMContext):
    """Показать очередь"""
    status = queue_mgr.get_status()
    
    await message.answer(
        queue_mgr.get_status_text(),
        reply_markup=queue_menu_keyboard(
            running=status['running'],
            count=status['pending'],
            interval=status['interval']
        ),
        parse_mode='HTML'
    )

@router.callback_query(F.data.startswith("q:"))
async def queue_callback(callback: CallbackQuery, state: FSMContext):
    """Обработка действий с очередью"""
    action = callback.data.split(":")[1]
    
    if action == "back":
        await callback.message.edit_text("👋 Главное меню")
        await callback.message.answer("Выберите действие:", reply_markup=main_menu())
        return
    
    if action == "menu":
        status = queue_mgr.get_status()
        await callback.message.edit_text(
            queue_mgr.get_status_text(),
            reply_markup=queue_menu_keyboard(
                running=status['running'],
                count=status['pending'],
                interval=status['interval']
            ),
            parse_mode='HTML'
        )
        return
    
    if action == "start":
        processor = MediaProcessor(callback.message.bot)
        success = await queue_mgr.start_queue(processor, callback.message.bot)
        
        if success:
            await callback.answer("✅ Очередь запущена")
            status = queue_mgr.get_status()
            await callback.message.edit_text(
                queue_mgr.get_status_text(),
                reply_markup=queue_menu_keyboard(
                    running=status['running'],
                    count=status['pending'],
                    interval=status['interval']
                ),
                parse_mode='HTML'
            )
        else:
            await callback.answer("❌ Нет постов для публикации", show_alert=True)
        return
    
    if action == "stop":
        await queue_mgr.stop_queue()
        await callback.answer("⏹ Остановлено")
        status = queue_mgr.get_status()
        await callback.message.edit_text(
            queue_mgr.get_status_text(),
            reply_markup=queue_menu_keyboard(
                running=status['running'],
                count=status['pending'],
                interval=status['interval']
            ),
            parse_mode='HTML'
        )
        return
    
    if action == "setint":
        await callback.message.answer(
            "⏱ <b>Настройка интервала</b>\n\n"
            "Введите интервал в минутах (1-1440):",
            parse_mode='HTML'
        )
        await state.set_state(QueueFlow.set_interval)
        return
    
    if action == "list":
        items = queue_mgr.get_queue()
        if not items:
            await callback.answer("Очередь пуста", show_alert=True)
            return
        
        await callback.message.edit_text(
            f"📋 <b>Очередь постов ({len(items)})</b>\n\n"
            "Нажмите на пост, чтобы удалить:",
            reply_markup=queue_list_keyboard(items),
            parse_mode='HTML'
        )
        return
    
    if action == "rm":
        index = int(callback.data.split(":")[2])
        queue_mgr.remove_from_queue(index)
        await callback.answer("🗑️ Удалено")
        
        items = queue_mgr.get_queue()
        if items:
            await callback.message.edit_text(
                f"📋 <b>Очередь постов ({len(items)})</b>\n\n"
                "Нажмите на пост, чтобы удалить:",
                reply_markup=queue_list_keyboard(items),
                parse_mode='HTML'
            )
        else:
            await callback.message.edit_text("📋 Очередь пуста")
        return
    
    if action == "clear":
        queue_mgr.clear_queue()
        await callback.answer("🗑️ Очередь очищена")
        status = queue_mgr.get_status()
        await callback.message.edit_text(
            queue_mgr.get_status_text(),
            reply_markup=queue_menu_keyboard(
                running=status['running'],
                count=status['pending'],
                interval=status['interval']
            ),
            parse_mode='HTML'
        )
        return

@router.message(QueueFlow.set_interval)
async def queue_interval_set(message: Message, state: FSMContext):
    """Установка интервала очереди"""
    try:
        interval = int(message.text)
        if 1 <= interval <= 1440:
            queue_mgr.set_interval(interval)
            await message.answer(
                f"✅ Интервал установлен: {interval} минут",
                reply_markup=main_menu()
            )
            await state.clear()
        else:
            await message.answer("❌ Введите число от 1 до 1440")
    except ValueError:
        await message.answer("❌ Введите корректное число")

# ============ ОЧИСТКА ЧАТА ============

@router.message(F.text == "🧹 Очистить чат")
async def clear_chat(message: Message, bot: Bot):
    """Очистка чата"""
    try:
        for i in range(1, 100):
            try:
                await bot.delete_message(
                    chat_id=message.chat.id,
                    message_id=message.message_id - i
                )
            except:
                pass
        await message.answer("✅ Чат очищен", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
