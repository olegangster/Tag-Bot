import logging
import asyncio
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional
import json
from pathlib import Path
import pytz

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

# Защита от двойной публикации
_publishing_users: set = set()


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
        raise ValueError(f"platform_id '{platform_id}' не найден в config.json")

    chat_id = int(platform_cfg["chat_id"])

    if not topic_key:
        return chat_id, None

    topics = platform_cfg.get("topics", {})
    topic_cfg = topics.get(topic_key)
    if not topic_cfg:
        raise ValueError(
            f"topic '{topic_key}' не найден в config.json для платформы '{platform_id}'."
        )

    thread_id = topic_cfg.get("thread_id")
    if thread_id is None:
        raise ValueError(f"У темы '{topic_key}' нет thread_id в config.json")

    return chat_id, int(thread_id)


def get_participant_data(user_id: int):
    config = load_config()
    for participant_key, data in config['participants'].items():
        if data.get('user_id') == user_id:
            return participant_key, data
    return None, None


def get_category_data(user_id: int, category_name: str):
    participant_key, participant_data = get_participant_data(user_id)
    if not participant_data:
        return None, None, None

    for cat_key, cat_data in participant_data['categories'].items():
        if cat_data['name'] == category_name or f"{cat_data['emoji']} {cat_data['name']}" == category_name:
            return cat_key, cat_data, participant_data['categories'][cat_key]
    return None, None, None


def is_channel_platform(platform_id: str) -> bool:
    """Проверяет, является ли платформа каналом"""
    config = load_config()
    platform_cfg = config.get("platforms", {}).get(platform_id, {})
    return platform_cfg.get("type", "group") == "channel"


def format_post_caption(category_data: Dict, selected_hashtags: List[str], platform_id: str, description: str = "") -> str:
    config = load_config()

    emoji = category_data['emoji']
    name = category_data['name'].upper()
    emoji_row = category_data.get('emoji_row', '')

    hashtag_lines = []
    for i in range(0, len(selected_hashtags), 3):
        line = ' '.join(selected_hashtags[i:i+3])
        hashtag_lines.append(line)
    hashtags_formatted = '\n'.join(hashtag_lines)

    platform_data = config['platforms'][platform_id]
    invite_link = platform_data.get('invite_link', 'https://t.me/your_channel')
    platform_type = platform_data.get('type', 'group')
    referral_bot = platform_data.get('referral_bot', '@Tag_Referral_bot')

    caption = f"{emoji} {name} {emoji}\n\n"
    caption += f"{emoji_row}\n"

    if selected_hashtags:
        caption += f"{hashtags_formatted}\n"
        caption += f"{emoji_row}\n\n"

    if description:
        caption += f"{description}\n\n"

    if platform_type == 'channel':
        caption += f"Получить бесплатно доступ к группе ➡️ {referral_bot}\n"

    caption += f"↘️⬇️↙️\n"
    caption += f'<a href="{invite_link}">Ссылка для друга</a>'

    return caption


def format_comic_post(category_data: Dict, comic_title: str, comic_link: str,
                      selected_hashtags: List[str], platform_id: str) -> str:
    config = load_config()

    emoji = category_data['emoji']
    name = category_data['name'].upper()
    emoji_row = category_data.get('emoji_row', '')

    platform_data = config['platforms'][platform_id]
    invite_link = platform_data.get('invite_link', 'https://t.me/your_channel')
    platform_type = platform_data.get('type', 'group')
    referral_bot = platform_data.get('referral_bot', '@Tag_Referral_bot')

    caption = f"{emoji} {name} {emoji}\n\n"
    caption += f"{emoji_row}\n\n"
    caption += f'📖 <b><u><a href="{comic_link}">{comic_title}</a></u></b>\n\n'

    if selected_hashtags:
        hashtag_lines = []
        for i in range(0, len(selected_hashtags), 3):
            line = ' '.join(selected_hashtags[i:i+3])
            hashtag_lines.append(line)
        hashtags_formatted = '\n'.join(hashtag_lines)
        caption += f"{hashtags_formatted}\n\n"

    caption += f"{emoji_row}\n\n"

    if platform_type == 'channel':
        caption += f"Получить бесплатно доступ к группе ➡️ {referral_bot}\n"

    caption += f"↘️⬇️↙️\n"
    caption += f'<a href="{invite_link}">Ссылка для друга</a>'

    return caption


async def safe_edit_status(message: Message, state: FSMContext, text: str):
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

    sent = await message.answer(text, parse_mode='HTML')
    await state.update_data(status_message_id=sent.message_id)


# ============ ГЛАВНОЕ МЕНЮ ============

@router.message(Command("start"))
@router.message(F.text == "🏠 Главная")
async def cmd_start(message: Message, state: FSMContext):
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
    is_comic_mode = cat_data.get('mode') == 'link_post'
    platform_id = cat_data['platform']

    await state.update_data(
        category_key=cat_key,
        category_data=cat_data,
        platform_id=platform_id,
        topic=cat_data.get('topic'),
        media_files=[],
        selected_hashtags=[],
        description="",
        is_comic_mode=is_comic_mode,
        stars_price=None,
        post_type="normal"
    )

    if is_comic_mode and not is_batch:
        await state.set_state(PostFlow.comic_link)
        await message.answer(
            f"✅ Категория: <b>{cat_data['emoji']} {cat_data['name']}</b>\n\n"
            "🔗 <b>Отправьте ссылку на комикс:</b>",
            reply_markup=comic_input_keyboard(),
            parse_mode='HTML'
        )
    elif is_channel_platform(platform_id) and not is_batch:
        # ✅ Для канала спрашиваем тип поста
        await state.set_state(PostFlow.selecting_post_type)
        await message.answer(
            f"✅ Категория: <b>{cat_data['emoji']} {cat_data['name']}</b>\n\n"
            "📌 <b>Выберите тип поста:</b>",
            reply_markup=post_type_keyboard(),
            parse_mode='HTML'
        )
    else:
        next_state = PostFlow.batch_uploading if is_batch else PostFlow.uploading_media
        await state.set_state(next_state)

        mode = "пакетной загрузки" if is_batch else "поста"
        await message.answer(
            f"✅ Категория: <b>{cat_data['emoji']} {cat_data['name']}</b>\n\n"
            f"📸 Отправьте фото или видео для {mode}:",
            reply_markup=media_upload_keyboard(0),
            parse_mode='HTML'
        )


# ============ ВЫБОР ТИПА ПОСТА (обычный / за звёзды) ============

@router.callback_query(F.data.startswith("posttype:"))
async def post_type_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]

    if action == "normal":
        await state.update_data(post_type="normal", stars_price=None)
        data = await state.get_data()
        await state.set_state(PostFlow.uploading_media)
        await callback.message.edit_text(
            "📸 <b>Отправьте фото или видео:</b>",
            parse_mode='HTML'
        )
        await callback.message.answer(
            "Отправьте медиафайлы:",
            reply_markup=media_upload_keyboard(0)
        )

    elif action == "stars":
        await state.update_data(post_type="stars")
        await state.set_state(PostFlow.entering_stars_price)
        await callback.message.edit_text(
            "⭐ <b>Пост за Звёзды Telegram</b>\n\n"
            "Введите количество звёзд за просмотр поста:\n\n"
            "💡 Минимум 1, максимум 2500 звёзд\n"
            "Например: <code>50</code>",
            parse_mode='HTML'
        )
        await callback.message.answer(
            "Введите количество звёзд:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            )
        )


@router.message(PostFlow.entering_stars_price)
async def stars_price_entered(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return

    try:
        price = int(message.text.strip())
        if price < 1 or price > 2500:
            await message.answer(
                "❌ Введите число от 1 до 2500 звёзд:",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="❌ Отмена")]],
                    resize_keyboard=True
                )
            )
            return

        await state.update_data(stars_price=price)
        await state.set_state(PostFlow.uploading_media)
        await message.answer(
            f"✅ Цена установлена: <b>{price} ⭐</b>\n\n"
            "📸 Теперь отправьте фото или видео:",
            reply_markup=media_upload_keyboard(0),
            parse_mode='HTML'
        )

    except ValueError:
        await message.answer(
            "❌ Введите корректное число:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            )
        )


# ============ ВЫБОР ТИПА ПОСТА ДЛЯ ПАКЕТНОЙ ЗАГРУЗКИ НА КАНАЛ ============

@router.callback_query(F.data.startswith("batch_posttype:"))
async def batch_post_type_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]

    if action == "normal":
        await state.update_data(post_type="normal", stars_price=None)
        # Переходим к выбору расписания
        data = await state.get_data()
        media_files = data.get('media_files', [])
        files_per_post = data.get('files_per_post', 1)
        posts_count = len(media_files) // files_per_post + (1 if len(media_files) % files_per_post else 0)
        await state.update_data(batch_step='schedule_type')
        await callback.message.edit_text(
            f"✅ Файлов в посте: <b>{files_per_post}</b>\n"
            f"📊 Получится постов: <b>{posts_count}</b>\n\n"
            "⏰ Выберите тип расписания:",
            reply_markup=schedule_type_keyboard(),
            parse_mode='HTML'
        )

    elif action == "stars":
        await state.update_data(post_type="stars")
        await state.set_state(PostFlow.entering_batch_stars_price)
        await callback.message.edit_text(
            "⭐ <b>Пакет за Звёзды Telegram</b>\n\n"
            "Введите количество звёзд за каждый пост:\n\n"
            "💡 Минимум 1, максимум 2500 звёзд",
            parse_mode='HTML'
        )
        await callback.message.answer(
            "Введите количество звёзд:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            )
        )


@router.message(PostFlow.entering_batch_stars_price)
async def batch_stars_price_entered(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return

    try:
        price = int(message.text.strip())
        if price < 1 or price > 2500:
            await message.answer("❌ Введите число от 1 до 2500:")
            return

        await state.update_data(stars_price=price)

        data = await state.get_data()
        media_files = data.get('media_files', [])
        files_per_post = data.get('files_per_post', 1)
        posts_count = len(media_files) // files_per_post + (1 if len(media_files) % files_per_post else 0)

        await state.set_state(PostFlow.batch_schedule)
        await state.update_data(batch_step='schedule_type')

        await message.answer(
            f"✅ Цена за пост: <b>{price} ⭐</b>\n\n"
            f"✅ Файлов в посте: <b>{files_per_post}</b>\n"
            f"📊 Получится постов: <b>{posts_count}</b>\n\n"
            "⏰ Выберите тип расписания:",
            reply_markup=schedule_type_keyboard(),
            parse_mode='HTML'
        )

    except ValueError:
        await message.answer("❌ Введите корректное число:")


# ============ РЕЖИМ КОМИКСОВ ============

@router.message(PostFlow.comic_link)
async def comic_link_received(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return

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
        "📝 <b>Теперь введите название комикса:</b>",
        reply_markup=comic_input_keyboard(),
        parse_mode='HTML'
    )


@router.message(PostFlow.comic_title)
async def comic_title_received(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return

    await state.update_data(comic_title=message.text)

    data = await state.get_data()
    comic_link = data.get('comic_link', '')
    comic_title = message.text
    category_data = data['category_data']
    platform_id = data['platform_id']

    preview = format_comic_post(
        category_data=category_data,
        comic_title=comic_title,
        comic_link=comic_link,
        selected_hashtags=[],
        platform_id=platform_id
    )

    await message.answer("📋 <b>Превью поста:</b>\n\n" + "─" * 30, parse_mode='HTML')
    await message.answer(preview, parse_mode='HTML', disable_web_page_preview=True)
    await message.answer("─" * 30 + "\n\nЧто дальше?", reply_markup=comic_confirm_keyboard(), parse_mode='HTML')


@router.callback_query(F.data.startswith("comic:"))
async def comic_actions(callback: CallbackQuery, state: FSMContext):
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

        caption = format_comic_post(
            category_data=category_data,
            comic_title=comic_title,
            comic_link=comic_link,
            selected_hashtags=selected_hashtags,
            platform_id=platform_id
        )

        try:
            chat_id, thread_id = resolve_chat_and_thread(platform_id, topic)
        except Exception as e:
            await callback.message.edit_text(f"❌ Ошибка: не найдена тема.\n\n{e}")
            return

        try:
            kwargs = dict(
                chat_id=chat_id,
                text=caption,
                parse_mode='HTML',
                disable_web_page_preview=False
            )
            if thread_id is not None:
                kwargs["message_thread_id"] = thread_id

            await callback.bot.send_message(**kwargs)
            await callback.message.edit_text("✅ Комикс опубликован!")
            await callback.message.answer("Что дальше?", reply_markup=after_post_keyboard())
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
            "🏷 <b>Выберите хештеги для комикса:</b>",
            reply_markup=hashtags_keyboard(all_hashtags, selected, callback.from_user.id, cat_key),
            parse_mode='HTML'
        )
        await state.set_state(PostFlow.comic_hashtags)
        return

    if action == "edit_title":
        await callback.message.answer("📝 <b>Введите новое название комикса:</b>",
                                      reply_markup=comic_input_keyboard(), parse_mode='HTML')
        await state.set_state(PostFlow.comic_title)
        return

    if action == "edit_link":
        await callback.message.answer("🔗 <b>Введите новую ссылку на комикс:</b>",
                                      reply_markup=comic_input_keyboard(), parse_mode='HTML')
        await state.set_state(PostFlow.comic_link)
        return

    if action == "cancel":
        await callback.message.edit_text("❌ Отменено")
        await callback.message.answer("Что дальше?", reply_markup=main_menu())
        await state.clear()
        return


# ============ ЗАГРУЗКА МЕДИА ============

async def handle_media_input(message: Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()

    async with user_locks[user_id]:
        data = await state.get_data()
        media_files = data.get('media_files', [])

        if message.photo:
            file_id = message.photo[-1].file_id
            unique_id = message.photo[-1].file_unique_id
            media_files.append(('photo', file_id, unique_id, message.message_id))
        elif message.video:
            file_id = message.video.file_id
            unique_id = message.video.file_unique_id
            media_files.append(('video', file_id, unique_id, message.message_id))

        if message.media_group_id:
            import time
            ts = time.time()
            await state.update_data(media_files=media_files, last_media_ts=ts)
            await asyncio.sleep(0.5)

            data2 = await state.get_data()
            if data2.get('last_media_ts') != ts:
                return
        else:
            await state.update_data(media_files=media_files)

        count = len(media_files)
        await safe_edit_status(
            message, state,
            f"✅ Загружено: <b>{count}</b> файл(ов)\n\n"
            "Отправьте еще или нажмите <b>➡️ Далее</b>"
        )


@router.message(PostFlow.uploading_media, F.photo | F.video)
@router.message(PostFlow.batch_uploading, F.photo | F.video)
async def media_received(message: Message, state: FSMContext):
    await handle_media_input(message, state)


# ============ КНОПКИ УПРАВЛЕНИЯ ЗАГРУЗКОЙ ============

@router.message(PostFlow.uploading_media, F.text.startswith("➡️ Далее"))
@router.message(PostFlow.batch_uploading, F.text.startswith("➡️ Далее"))
async def media_done(message: Message, state: FSMContext):
    data = await state.get_data()
    media_files = data.get('media_files', [])

    if not media_files:
        await message.answer("❌ Сначала отправьте хотя бы один файл")
        return

    current_state = await state.get_state()
    is_batch = current_state == PostFlow.batch_uploading

    if is_batch:
        platform_id = data.get('platform_id', '')
        # Для канала в пакетном режиме спрашиваем тип поста
        if is_channel_platform(platform_id):
            await state.set_state(PostFlow.batch_schedule)
            await state.update_data(batch_step='post_type')
            await message.answer(
                f"📊 Загружено <b>{len(media_files)}</b> файлов\n\n"
                "⚙️ Сколько файлов объединять в один пост?",
                reply_markup=files_per_post_keyboard(),
                parse_mode='HTML'
            )
        else:
            await state.set_state(PostFlow.batch_schedule)
            await state.update_data(batch_step='files_per_post')
            await message.answer(
                f"📊 Загружено <b>{len(media_files)}</b> файлов\n\n"
                "⚙️ Сколько файлов объединять в один пост?",
                reply_markup=files_per_post_keyboard(),
                parse_mode='HTML'
            )
    else:
        category_data = data['category_data']
        all_hashtags = category_data.get('hashtags', [])
        cat_key = data['category_key']

        await state.set_state(PostFlow.selecting_hashtags)
        await message.answer(
            "🏷 <b>Выберите хештеги:</b>",
            reply_markup=hashtags_keyboard(all_hashtags, [], message.from_user.id, cat_key),
            parse_mode='HTML'
        )


@router.message(PostFlow.uploading_media, F.text == "🗑️ Очистить всё")
@router.message(PostFlow.batch_uploading, F.text == "🗑️ Очистить всё")
async def clear_media(message: Message, state: FSMContext):
    await state.update_data(media_files=[])
    await message.answer("🗑️ Файлы очищены\n\nОтправьте новые фото или видео:",
                         reply_markup=media_upload_keyboard(0))


@router.message(PostFlow.uploading_media, F.text == "❌ Отмена")
@router.message(PostFlow.batch_uploading, F.text == "❌ Отмена")
async def cancel_upload(message: Message, state: FSMContext):
    await cmd_start(message, state)


# ============ СОХРАНЕНИЕ В ЧЕРНОВИК/ОЧЕРЕДЬ ============

@router.message(PostFlow.uploading_media, F.text == "💾 В черновик")
@router.message(PostFlow.batch_uploading, F.text == "💾 В черновик")
async def save_to_draft_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    media_files = data.get('media_files', [])

    if not media_files:
        await message.answer("❌ Сначала загрузите файлы")
        return

    await state.update_data(saving_to='draft')
    await message.answer(
        "📝 <b>Введите название черновика:</b>",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        ),
        parse_mode='HTML'
    )


@router.message(PostFlow.uploading_media, F.text == "📥 В очередь")
@router.message(PostFlow.batch_uploading, F.text == "📥 В очередь")
async def save_to_queue_prompt(message: Message, state: FSMContext):
    data = await state.get_data()
    media_files = data.get('media_files', [])

    if not media_files:
        await message.answer("❌ Сначала загрузите файлы")
        return

    category_data = data['category_data']
    all_hashtags = category_data.get('hashtags', [])
    cat_key = data['category_key']

    await state.update_data(saving_to='queue')
    await state.set_state(PostFlow.selecting_hashtags)
    await message.answer(
        "📥 <b>Добавление в очередь</b>\n\n"
        "🏷 Выберите хештеги:",
        reply_markup=hashtags_keyboard(all_hashtags, [], message.from_user.id, cat_key),
        parse_mode='HTML'
    )


@router.message(PostFlow.uploading_media)
@router.message(PostFlow.batch_uploading)
async def save_named_item(message: Message, state: FSMContext):
    data = await state.get_data()
    saving_to = data.get('saving_to')

    if not saving_to:
        return

    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return

    name = message.text

    if saving_to == 'draft':
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


# ============ ХЕШТЕГИ ============

@router.callback_query(F.data.startswith("tag:"))
async def hashtag_selection(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category_data = data['category_data']
    all_hashtags = category_data.get('hashtags', [])
    selected = data.get('selected_hashtags', [])
    cat_key = data['category_key']

    action = callback.data[4:]

    if action == "select_all":
        selected = all_hashtags.copy()
        await state.update_data(selected_hashtags=selected)
        await callback.answer("✅ Все выбраны")
        await callback.message.edit_reply_markup(
            reply_markup=hashtags_keyboard(all_hashtags, selected, callback.from_user.id, cat_key)
        )
        return

    if action == "clear_all":
        selected = []
        await state.update_data(selected_hashtags=selected)
        await callback.answer("❌ Сброшено")
        await callback.message.edit_reply_markup(
            reply_markup=hashtags_keyboard(all_hashtags, selected, callback.from_user.id, cat_key)
        )
        return

    if action == "templates":
        await callback.message.edit_text(
            "📋 <b>Мои шаблоны хештегов:</b>",
            reply_markup=hashtag_templates_keyboard(callback.from_user.id, cat_key, selected),
            parse_mode='HTML'
        )
        return

    if action == "back":
        await callback.message.edit_text(
            "🏷 <b>Выберите хештеги:</b>",
            reply_markup=hashtags_keyboard(all_hashtags, selected, callback.from_user.id, cat_key),
            parse_mode='HTML'
        )
        return

    if action == "add_custom":
        await callback.message.answer(
            "➕ <b>Добавление своего хештега</b>\n\n"
            "Введите хештег (например: #myhashtag):",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            ),
            parse_mode='HTML'
        )
        await state.set_state(HashtagFlow.adding_custom)
        return

    if action == "save_template":
        if not selected:
            await callback.answer("❌ Выберите хотя бы один хештег", show_alert=True)
            return

        await callback.message.answer(
            "💾 <b>Сохранение шаблона</b>\n\n"
            f"Выбрано хештегов: {len(selected)}\n\n"
            "Введите название шаблона:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            ),
            parse_mode='HTML'
        )
        await state.set_state(HashtagFlow.naming_template)
        return

    if action == "done":
        current_state = await state.get_state()
        saving_to = data.get('saving_to')
        is_comic = data.get('is_comic_mode', False)

        if current_state == PostFlow.comic_hashtags or is_comic:
            await state.update_data(selected_hashtags=selected)
            comic_link = data.get('comic_link', '')
            comic_title = data.get('comic_title', '')
            platform_id = data['platform_id']

            preview = format_comic_post(
                category_data=category_data, comic_title=comic_title,
                comic_link=comic_link, selected_hashtags=selected, platform_id=platform_id
            )

            await callback.message.edit_text("📋 <b>Обновленное превью:</b>", parse_mode='HTML')
            await callback.message.answer(preview, parse_mode='HTML', disable_web_page_preview=True)
            await callback.message.answer("Что дальше?", reply_markup=comic_confirm_keyboard())
            return

        if saving_to == 'queue':
            await state.update_data(selected_hashtags=selected)
            await callback.message.edit_text(
                f"✅ Выбрано хештегов: {len(selected)}\n\nВведите название для очереди:"
            )
            await callback.message.answer(
                "Введите название:",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="❌ Отмена")]],
                    resize_keyboard=True
                )
            )
            return

        batch_step = data.get('batch_step')
        if batch_step == 'confirm':
            await state.update_data(selected_hashtags=selected)

            posts = data.get('posts', [])
            schedule = data.get('schedule', [])
            time_category = data.get('time_category', 'minute')
            time_value = data.get('time_value', 60)
            stars_price = data.get('stars_price')

            batch_service = BatchPostingService(MediaProcessor(callback.message.bot), callback.message.bot)
            summary = batch_service.get_batch_summary(len(posts), schedule, time_category, time_value)

            stars_text = f"\n⭐ Цена за пост: <b>{stars_price} звёзд</b>" if stars_price else ""

            await callback.message.edit_text(
                summary + f"\n\n✅ Хештегов выбрано: <b>{len(selected)}</b>{stars_text}\n\n"
                "<b>❓ Опубликовать пакет?</b>",
                reply_markup=confirm_batch_final_keyboard(len(posts)),
                parse_mode='HTML'
            )
            return

        await state.update_data(selected_hashtags=selected)

        config = load_config()
        description_enabled = config.get('features', {}).get('post_description', False)

        if description_enabled:
            await callback.message.edit_text(
                f"✅ Выбрано хештегов: <b>{len(selected)}</b>\n\n"
                "📝 <b>Добавить описание к посту?</b>\n\n"
                "Введите текст или нажмите <b>Пропустить</b>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⏭ Пропустить", callback_data="desc:skip")],
                ]),
                parse_mode='HTML'
            )
            await state.set_state(PostFlow.adding_description)
        else:
            await publish_post(callback.message, state, callback.from_user.id)
        return

    if action in all_hashtags:
        if action in selected:
            selected.remove(action)
        else:
            selected.append(action)

        await state.update_data(selected_hashtags=selected)
        await callback.message.edit_reply_markup(
            reply_markup=hashtags_keyboard(all_hashtags, selected, callback.from_user.id, cat_key)
        )
        return


# ============ ШАБЛОНЫ ХЕШТЕГОВ ============

@router.callback_query(F.data.startswith("tmpl:"))
async def apply_hashtag_template(callback: CallbackQuery, state: FSMContext):
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

        selected = data.get('selected_hashtags', [])
        is_active = all(tag in selected for tag in template_tags)

        if is_active:
            selected = [tag for tag in selected if tag not in template_tags]
            await callback.answer(f"❌ Шаблон \"{template_name}\" снят")
        else:
            for tag in template_tags:
                if tag not in selected:
                    selected.append(tag)
            await callback.answer(f"✅ Шаблон \"{template_name}\" применен")

        await state.update_data(selected_hashtags=selected)
        await callback.message.edit_reply_markup(
            reply_markup=hashtag_templates_keyboard(callback.from_user.id, cat_key, selected)
        )
        return


# ============ ПОЛЬЗОВАТЕЛЬСКИЕ ХЕШТЕГИ ============

@router.message(HashtagFlow.adding_custom)
async def custom_hashtag_received(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        data = await state.get_data()
        category_data = data['category_data']
        all_hashtags = category_data.get('hashtags', [])
        selected = data.get('selected_hashtags', [])
        cat_key = data['category_key']

        await state.set_state(PostFlow.selecting_hashtags)
        await message.answer(
            "🏷 <b>Выберите хештеги:</b>",
            reply_markup=hashtags_keyboard(all_hashtags, selected, message.from_user.id, cat_key),
            parse_mode='HTML'
        )
        return

    hashtag = message.text.strip()

    if not hashtag.startswith('#'):
        await message.answer("❌ Хештег должен начинаться с #\n\nПопробуйте еще раз:")
        return

    if ' ' in hashtag:
        await message.answer("❌ Хештег не должен содержать пробелов\n\nПопробуйте еще раз:")
        return

    data = await state.get_data()
    category_data = data['category_data']
    cat_key = data['category_key']

    config = load_config()

    for participant_key, participant_data in config['participants'].items():
        if participant_data.get('user_id') == message.from_user.id:
            if cat_key in participant_data['categories']:
                if hashtag not in participant_data['categories'][cat_key]['hashtags']:
                    participant_data['categories'][cat_key]['hashtags'].append(hashtag)
                    save_config(config)
                    category_data['hashtags'].append(hashtag)
                    await state.update_data(category_data=category_data)
                    selected = data.get('selected_hashtags', [])
                    selected.append(hashtag)
                    await state.update_data(selected_hashtags=selected)
                    await message.answer(f"✅ Хештег <b>{hashtag}</b> добавлен и выбран!", parse_mode='HTML')
                else:
                    selected = data.get('selected_hashtags', [])
                    if hashtag not in selected:
                        selected.append(hashtag)
                        await state.update_data(selected_hashtags=selected)
                    await message.answer(f"ℹ️ Хештег <b>{hashtag}</b> уже существует и выбран!", parse_mode='HTML')
                break

    all_hashtags = category_data.get('hashtags', [])
    selected = data.get('selected_hashtags', [])

    await state.set_state(PostFlow.selecting_hashtags)
    await message.answer(
        "🏷 <b>Выберите хештеги:</b>",
        reply_markup=hashtags_keyboard(all_hashtags, selected, message.from_user.id, cat_key),
        parse_mode='HTML'
    )


# ============ СОХРАНЕНИЕ ШАБЛОНОВ ============

@router.message(HashtagFlow.naming_template)
async def save_hashtag_template(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        data = await state.get_data()
        category_data = data['category_data']
        all_hashtags = category_data.get('hashtags', [])
        selected = data.get('selected_hashtags', [])
        cat_key = data['category_key']

        await state.set_state(PostFlow.selecting_hashtags)
        await message.answer(
            "🏷 <b>Выберите хештеги:</b>",
            reply_markup=hashtags_keyboard(all_hashtags, selected, message.from_user.id, cat_key),
            parse_mode='HTML'
        )
        return

    template_name = message.text.strip()
    data = await state.get_data()
    selected = data.get('selected_hashtags', [])
    cat_key = data['category_key']

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

    category_data = data['category_data']
    all_hashtags = category_data.get('hashtags', [])

    await state.set_state(PostFlow.selecting_hashtags)
    await message.answer(
        "🏷 <b>Выберите хештеги:</b>",
        reply_markup=hashtags_keyboard(all_hashtags, selected, message.from_user.id, cat_key, show_templates=True),
        parse_mode='HTML'
    )


# ============ ОПИСАНИЕ ПОСТА ============

@router.callback_query(F.data == "desc:skip")
async def description_skip(callback: CallbackQuery, state: FSMContext):
    await publish_post(callback.message, state, callback.from_user.id)


@router.message(PostFlow.adding_description)
async def description_received(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cmd_start(message, state)
        return

    description = message.text.strip()
    await state.update_data(description=description)

    status = await message.answer("⏳ Публикация...")
    await state.update_data(status_message_id=status.message_id)
    await publish_post(status, state, message.from_user.id)


# ============ ПУБЛИКАЦИЯ ПОСТА ============

async def publish_post(message: Message, state: FSMContext, user_id: int):
    if user_id in _publishing_users:
        logger.warning(f"⚠️ user {user_id} уже публикует — игнорируем")
        try:
            await message.answer("⏳ Публикация уже идёт, подождите...")
        except Exception:
            pass
        return

    _publishing_users.add(user_id)
    try:
        await _do_publish_post(message, state, user_id)
    finally:
        _publishing_users.discard(user_id)


async def _do_publish_post(message: Message, state: FSMContext, user_id: int):
    data = await state.get_data()

    media_files = data.get('media_files', [])
    selected_hashtags = data.get('selected_hashtags', [])
    category_data = data['category_data']
    platform_id = data['platform_id']
    topic = data.get('topic')
    description = data.get('description', '')
    stars_price = data.get('stars_price')
    post_type = data.get('post_type', 'normal')

    if not media_files:
        await message.answer("❌ Нет файлов для публикации")
        return

    processor = MediaProcessor(bot=message.bot)

    try:
        await message.edit_text("⏳ Обработка файлов...")
    except Exception:
        pass

    media_files.sort(key=lambda x: x[3] if len(x) > 3 else 0)
    media_to_process = [(mtype, fid) for mtype, fid, *_ in media_files]
    processed_media = await processor.process_batch(media_to_process)

    if not processed_media:
        try:
            await message.edit_text("❌ Ошибка обработки файлов")
        except Exception:
            await message.answer("❌ Ошибка обработки файлов")
        return

    caption = format_post_caption(category_data, selected_hashtags, platform_id, description)

    try:
        chat_id, thread_id = resolve_chat_and_thread(platform_id, topic)
    except Exception as e:
        logger.error(f"❌ Неверная тема: platform={platform_id}, topic={topic} -> {e}")
        try:
            await message.edit_text(f"❌ Ошибка: не найдена тема.\n\n{e}")
        except Exception:
            await message.answer(f"❌ Ошибка: не найдена тема.\n\n{e}")
        return

    try:
        await message.edit_text("📤 Публикация...")
    except Exception:
        pass

    try:
        if post_type == "stars" and stars_price and stars_price > 0:
            # Пост за звёзды
            success = await _publish_stars_post(
                bot=message.bot,
                chat_id=chat_id,
                thread_id=thread_id,
                processed_media=processed_media,
                caption=caption,
                stars_price=stars_price,
                processor=processor
            )
        else:
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
            try:
                await message.edit_text("✅ Пост успешно опубликован!")
            except Exception:
                pass
            await message.answer("Что дальше?", reply_markup=after_post_keyboard())
            await state.clear()
        else:
            try:
                await message.edit_text("❌ Ошибка публикации")
            except Exception:
                await message.answer("❌ Ошибка публикации")

    except Exception as e:
        logger.error(f"Ошибка публикации: {e}")
        try:
            await message.edit_text(f"❌ Ошибка: {str(e)}")
        except Exception:
            await message.answer(f"❌ Ошибка: {str(e)}")


async def _publish_stars_post(bot, chat_id, thread_id, processed_media, caption, stars_price, processor) -> bool:
    """Публикует пост за звёзды Telegram"""
    from aiogram.types import LabeledPrice

    try:
        # Сначала отправляем медиа
        if len(processed_media) == 1:
            await processor.send_single_media(
                chat_id, processed_media[0][0], processed_media[0][1], None, thread_id
            )
        else:
            await processor.send_media_group(chat_id, processed_media, None, thread_id)

        # Затем отправляем invoice за звёзды
        kwargs = dict(
            chat_id=chat_id,
            title="⭐ Эксклюзивный контент",
            description=caption[:255] if caption else "Эксклюзивный контент за звёзды",
            payload="stars_exclusive_content",
            currency="XTR",
            prices=[LabeledPrice(label="Звёзды Telegram", amount=stars_price)],
        )
        if thread_id is not None:
            kwargs["message_thread_id"] = thread_id

        await bot.send_invoice(**kwargs)
        logger.info(f"✅ Пост за звёзды опубликован: {stars_price} XTR")
        return True

    except Exception as e:
        logger.error(f"Ошибка публикации за звёзды: {e}")
        return False


# ============ СОХРАНЕНИЕ В ОЧЕРЕДЬ С НАЗВАНИЕМ ============

@router.message(PostFlow.selecting_hashtags)
async def save_to_queue_with_name(message: Message, state: FSMContext):
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
    await callback.answer()
    files_per_post = int(callback.data.split(":")[1])

    data = await state.get_data()
    media_files = data.get('media_files', [])
    posts_count = len(media_files) // files_per_post + (1 if len(media_files) % files_per_post else 0)

    await state.update_data(files_per_post=files_per_post)

    platform_id = data.get('platform_id', '')

    # Для канала в пакетном режиме спрашиваем тип поста
    if is_channel_platform(platform_id):
        await state.update_data(batch_step='post_type_check')
        await callback.message.edit_text(
            f"✅ Файлов в посте: <b>{files_per_post}</b>\n"
            f"📊 Получится постов: <b>{posts_count}</b>\n\n"
            "📌 <b>Выберите тип постов в пакете:</b>",
            reply_markup=batch_post_type_keyboard(),
            parse_mode='HTML'
        )
    else:
        await state.update_data(batch_step='schedule_type', post_type='normal', stars_price=None)
        await callback.message.edit_text(
            f"✅ Файлов в посте: <b>{files_per_post}</b>\n"
            f"📊 Получится постов: <b>{posts_count}</b>\n\n"
            "⏰ Выберите тип расписания:",
            reply_markup=schedule_type_keyboard(),
            parse_mode='HTML'
        )


@router.callback_query(F.data.startswith("time:category:"))
async def batch_time_category(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    time_cat = callback.data.split(":")[-1]

    await state.update_data(time_category=time_cat, batch_step='time_value')

    await callback.message.edit_text(
        "⏱ Выберите длительность:",
        reply_markup=time_options_keyboard(time_cat)
    )


@router.callback_query(F.data == "time:back")
async def batch_time_back(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(batch_step='time_category')

    await callback.message.edit_text(
        "⏰ За какой период публиковать?",
        reply_markup=batch_schedule_keyboard()
    )


# ============ ВЫБОР ТИПА РАСПИСАНИЯ ============

@router.callback_query(F.data.startswith("sched_type:"))
async def schedule_type_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    sched_type = callback.data.split(":")[1]

    await state.update_data(schedule_type=sched_type)

    if sched_type == "normal":
        await state.update_data(batch_step='time_category')
        await callback.message.edit_text(
            "⏰ За какой период публиковать?",
            reply_markup=batch_schedule_keyboard()
        )
    elif sched_type == "custom":
        await state.update_data(selected_dates=[])
        await state.set_state(PostFlow.custom_schedule_selecting_dates)
        await callback.message.edit_text(
            "📅 <b>Создание графика публикаций</b>\n\n"
            "🗓 <b>Шаг 1/3:</b> Выберите дни для публикации постов\n\n"
            "💡 Можно выбрать несколько дней",
            reply_markup=date_selection_keyboard([]),
            parse_mode='HTML'
        )


# ============ СОЗДАНИЕ ГРАФИКА - ВЫБОР ДАТ ============

@router.callback_query(F.data.startswith("customdate:"))
async def custom_schedule_date_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    data = await state.get_data()
    selected_dates = data.get('selected_dates', [])

    if action == "toggle":
        date_str = callback.data.split(":")[2]
        if date_str in selected_dates:
            selected_dates.remove(date_str)
        else:
            selected_dates.append(date_str)

        await state.update_data(selected_dates=selected_dates)
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=date_selection_keyboard(selected_dates))
        return

    elif action == "all":
        kyiv_tz = pytz.timezone('Europe/Kiev')
        now = datetime.now(kyiv_tz)
        selected_dates = [(now + timedelta(days=i)).strftime("%d.%m") for i in range(14)]
        await state.update_data(selected_dates=selected_dates)
        await callback.answer(f"✅ Выбрано {len(selected_dates)} дней")
        await callback.message.edit_reply_markup(reply_markup=date_selection_keyboard(selected_dates))
        return

    elif action == "clear":
        selected_dates = []
        await state.update_data(selected_dates=selected_dates)
        await callback.message.edit_reply_markup(reply_markup=date_selection_keyboard(selected_dates))
        await callback.answer("❌ Выбор очищен")
        return

    elif action == "done":
        fresh_data = await state.get_data()
        selected_dates = fresh_data.get('selected_dates', [])

        if not selected_dates:
            await callback.answer("❌ Выберите хотя бы один день!", show_alert=True)
            return

        await callback.answer(f"✅ Выбрано {len(selected_dates)} дней")
        await state.set_state(PostFlow.custom_schedule_posts_per_day)

        media_files = fresh_data.get('media_files', [])
        files_per_post = fresh_data.get('files_per_post', 1)
        total_posts = len(media_files) // files_per_post + (1 if len(media_files) % files_per_post else 0)

        await callback.message.edit_text(
            f"✅ Выбрано дней: <b>{len(selected_dates)}</b>\n\n"
            f"📊 Всего постов: <b>{total_posts}</b>\n\n"
            f"🗓 <b>Шаг 2/3:</b> Сколько постов публиковать в день?",
            reply_markup=posts_per_day_keyboard(),
            parse_mode='HTML'
        )
        return

    elif action == "back":
        await callback.answer()
        await state.update_data(batch_step='schedule_type')

        fresh_data = await state.get_data()
        media_files = fresh_data.get('media_files', [])
        files_per_post = fresh_data.get('files_per_post', 1)
        posts_count = len(media_files) // files_per_post + (1 if len(media_files) % files_per_post else 0)

        await callback.message.edit_text(
            f"✅ Файлов в посте: <b>{files_per_post}</b>\n"
            f"📊 Получится постов: <b>{posts_count}</b>\n\n"
            "⏰ Выберите тип расписания:",
            reply_markup=schedule_type_keyboard(),
            parse_mode='HTML'
        )
        return


# ============ СОЗДАНИЕ ГРАФИКА - ПОСТЫ В ДЕНЬ ============

@router.callback_query(F.data.startswith("postsperday:"))
async def custom_schedule_posts_per_day_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]

    if action == "back":
        data = await state.get_data()
        selected_dates = data.get('selected_dates', [])

        await state.set_state(PostFlow.custom_schedule_selecting_dates)
        await callback.message.edit_text(
            "📅 <b>Создание графика публикаций</b>\n\n"
            "🗓 <b>Шаг 1/3:</b> Выберите дни для публикации постов",
            reply_markup=date_selection_keyboard(selected_dates),
            parse_mode='HTML'
        )
        return

    posts_per_day = int(action)
    await state.update_data(posts_per_day=posts_per_day)
    await state.set_state(PostFlow.custom_schedule_selecting_times)

    data = await state.get_data()
    selected_dates = data.get('selected_dates', [])

    await callback.message.edit_text(
        f"✅ Дней: <b>{len(selected_dates)}</b>\n"
        f"✅ Постов в день: <b>{posts_per_day}</b>\n\n"
        f"🗓 <b>Шаг 3/3:</b> Выберите время для публикации\n\n"
        f"💡 Выберите готовый вариант или введите своё время:",
        reply_markup=time_slots_keyboard(posts_per_day, []),
        parse_mode='HTML'
    )


# ============ СОЗДАНИЕ ГРАФИКА - ВЫБОР ВРЕМЕНИ ============

@router.callback_query(F.data.startswith("timeslot:"))
async def custom_schedule_time_slot_action(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    action = parts[1]
    data = await state.get_data()
    posts_per_day = data.get('posts_per_day', 1)

    if action == "preset":
        preset_index = int(parts[2])

        if posts_per_day == 1:
            presets = [["12:00"], ["15:00"], ["18:00"], ["20:00"]]
        elif posts_per_day == 2:
            presets = [["10:00", "18:00"], ["12:00", "20:00"], ["14:00", "22:00"]]
        elif posts_per_day == 3:
            presets = [["10:00", "15:00", "20:00"], ["12:00", "16:00", "20:00"], ["14:00", "18:00", "22:00"]]
        elif posts_per_day == 4:
            presets = [["09:00", "13:00", "17:00", "21:00"], ["10:00", "14:00", "18:00", "22:00"],
                       ["11:00", "15:00", "19:00", "23:00"]]
        elif posts_per_day == 5:
            presets = [["09:00", "12:00", "15:00", "18:00", "21:00"],
                       ["10:00", "13:00", "16:00", "19:00", "22:00"]]
        else:
            interval_hours = 24 // posts_per_day
            presets = []
            for start in [8, 9, 10]:
                times = []
                for i in range(posts_per_day):
                    hour = (start + i * interval_hours) % 24
                    times.append(f"{hour:02d}:00")
                presets.append(times)

        selected_times = presets[preset_index] if preset_index < len(presets) else []
        await state.update_data(selected_times=selected_times)
        await callback.answer(f"✅ {', '.join(selected_times)}")
        await callback.message.edit_reply_markup(reply_markup=time_slots_keyboard(posts_per_day, selected_times))
        return

    elif action == "custom":
        await callback.answer()
        await state.set_state(PostFlow.custom_schedule_input_time)
        await callback.message.answer(
            f"✏️ <b>Ввод времени</b>\n\n"
            f"Введите {posts_per_day} времен(и) через запятую\n\n"
            f"<b>Формат:</b> ЧЧ:ММ\n"
            f"<b>Пример:</b> 10:00, 15:00, 20:00\n\n"
            f"💡 Время киевское (UTC+2/+3)",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            ),
            parse_mode='HTML'
        )
        return

    elif action == "done":
        fresh_data = await state.get_data()
        selected_times = fresh_data.get('selected_times', [])
        selected_dates = fresh_data.get('selected_dates', [])

        if not selected_times:
            await callback.answer("❌ Выберите время публикации!", show_alert=True)
            return

        schedule = generate_custom_schedule(selected_dates, selected_times)

        media_files = fresh_data.get('media_files', [])
        media_files.sort(key=lambda x: x[3] if len(x) > 3 else 0)
        files_per_post = fresh_data.get('files_per_post', 1)

        posts = []
        for i in range(0, len(media_files), files_per_post):
            posts.append(media_files[i:i + files_per_post])

        if len(schedule) < len(posts):
            await callback.answer(
                f"⚠️ Недостаточно слотов! Постов: {len(posts)}, слотов: {len(schedule)}",
                show_alert=True
            )
            return

        schedule = schedule[:len(posts)]

        await state.update_data(
            schedule=schedule, posts=posts, batch_step='confirm',
            time_category='custom', time_value=len(selected_dates)
        )

        await callback.answer(f"✅ График создан: {len(posts)} постов")

        summary_text = (
            f"📦 <b>График публикаций создан!</b>\n\n"
            f"📊 Постов: <b>{len(posts)}</b>\n"
            f"📅 Дней: <b>{len(selected_dates)}</b>\n"
            f"⏰ Время: <b>{', '.join(selected_times)}</b>\n\n"
            f"🕐 Первый пост: <b>{schedule[0].strftime('%d.%m %H:%M')}</b>\n"
        )
        if len(posts) > 1:
            summary_text += f"🕐 Последний пост: <b>{schedule[-1].strftime('%d.%m %H:%M')}</b>\n"

        summary_text += "\n\n<b>🔀 Перемешать посты?</b>\n\n"
        summary_text += "➡️ <i>Нет</i> - посты в порядке загрузки\n"
        summary_text += "🔀 <i>Да</i> - случайный порядок"

        await callback.message.edit_text(summary_text, reply_markup=batch_shuffle_keyboard(), parse_mode='HTML')
        return

    elif action == "back":
        await callback.answer()
        await state.set_state(PostFlow.custom_schedule_posts_per_day)

        fresh = await state.get_data()
        media_files = fresh.get('media_files', [])
        files_per_post = fresh.get('files_per_post', 1)
        total_posts = len(media_files) // files_per_post + (1 if len(media_files) % files_per_post else 0)
        selected_dates = fresh.get('selected_dates', [])

        await callback.message.edit_text(
            f"✅ Выбрано дней: <b>{len(selected_dates)}</b>\n\n"
            f"📊 Всего постов: <b>{total_posts}</b>\n\n"
            f"🗓 <b>Шаг 2/3:</b> Сколько постов публиковать в день?",
            reply_markup=posts_per_day_keyboard(),
            parse_mode='HTML'
        )
        return


@router.message(PostFlow.custom_schedule_input_time)
async def custom_schedule_time_input(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        data = await state.get_data()
        posts_per_day = data.get('posts_per_day', 1)

        await state.set_state(PostFlow.custom_schedule_selecting_times)
        await message.answer(
            f"🗓 <b>Шаг 3/3:</b> Выберите время для публикации",
            reply_markup=time_slots_keyboard(posts_per_day, []),
            parse_mode='HTML'
        )
        return

    try:
        times_str = message.text.strip()
        times = [t.strip() for t in times_str.split(',')]

        valid_times = []
        for time_str in times:
            if ':' not in time_str:
                raise ValueError(f"Неправильный формат: {time_str}")
            hour, minute = time_str.split(':')
            hour = int(hour)
            minute = int(minute)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"Неправильное время: {time_str}")
            valid_times.append(f"{hour:02d}:{minute:02d}")

        data = await state.get_data()
        posts_per_day = data.get('posts_per_day', 1)

        if len(valid_times) != posts_per_day:
            await message.answer(
                f"❌ Нужно ввести <b>{posts_per_day}</b> времен(и), введено {len(valid_times)}\n\nПопробуйте ещё раз:",
                parse_mode='HTML'
            )
            return

        valid_times.sort()
        await state.update_data(selected_times=valid_times)
        await state.set_state(PostFlow.custom_schedule_selecting_times)

        await message.answer(
            f"✅ Время установлено: <b>{', '.join(valid_times)}</b>",
            reply_markup=time_slots_keyboard(posts_per_day, valid_times),
            parse_mode='HTML'
        )

    except Exception as e:
        await message.answer(
            f"❌ Ошибка в формате времени!\n\n"
            f"<b>Правильный формат:</b> ЧЧ:ММ\n"
            f"<b>Пример:</b> 10:00, 15:00, 20:00\n\nПопробуйте ещё раз:",
            parse_mode='HTML'
        )


def generate_custom_schedule(selected_dates: List[str], selected_times: List[str]) -> List[datetime]:
    """Генерирует расписание по выбранным датам и временам (timezone-aware)"""
    kyiv_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kyiv_tz)
    schedule = []

    dates_dt = []
    for date_str in selected_dates:
        day, month = map(int, date_str.split('.'))
        for i in range(365):
            check_date = now + timedelta(days=i)
            if check_date.day == day and check_date.month == month:
                dates_dt.append(check_date.date())
                break

    dates_dt.sort()

    for date in dates_dt:
        for time_str in selected_times:
            hour, minute = map(int, time_str.split(':'))
            dt = kyiv_tz.localize(datetime.combine(date, datetime.min.time().replace(hour=hour, minute=minute)))
            if dt > now:
                schedule.append(dt)

    schedule.sort()
    return schedule


@router.callback_query(F.data.startswith("schedule:"))
async def batch_schedule_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
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
    media_files.sort(key=lambda x: x[3] if len(x) > 3 else 0)
    files_per_post = data.get('files_per_post', 1)

    posts = []
    for i in range(0, len(media_files), files_per_post):
        posts.append(media_files[i:i + files_per_post])

    posts_count = len(posts)

    batch_service = BatchPostingService(MediaProcessor(callback.message.bot), callback.message.bot)
    schedule = batch_service.generate_schedule(posts_count, time_category, time_value)

    await state.update_data(
        time_category=time_category, time_value=time_value,
        schedule=schedule, posts=posts, batch_step='confirm'
    )

    summary = batch_service.get_batch_summary(posts_count, schedule, time_category, time_value)

    await callback.message.edit_text(
        summary + "\n\n<b>🔀 Перемешать посты?</b>\n\n"
        "➡️ <i>Нет</i> - в порядке загрузки\n"
        "🔀 <i>Да</i> - случайный порядок",
        reply_markup=batch_shuffle_keyboard(),
        parse_mode='HTML'
    )


@router.callback_query(F.data.startswith("shuffle:"))
async def batch_shuffle_selected(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]

    if action == "cancel":
        await callback.message.edit_text("❌ Отменено")
        await callback.message.answer("Что дальше?", reply_markup=main_menu())
        await state.clear()
        return

    shuffle = (action == "yes")
    await state.update_data(shuffle=shuffle)

    data = await state.get_data()
    posts = data.get('posts', [])
    schedule = data.get('schedule', [])
    time_category = data.get('time_category', 'minute')
    time_value = data.get('time_value', 60)

    batch_service = BatchPostingService(MediaProcessor(callback.message.bot), callback.message.bot)
    summary = batch_service.get_batch_summary(len(posts), schedule, time_category, time_value)

    shuffle_text = "🔀 Да, перемешать" if shuffle else "➡️ Нет, по порядку"

    await callback.message.edit_text(
        summary + f"\n✅ Порядок: <b>{shuffle_text}</b>",
        parse_mode='HTML'
    )

    category_data = data['category_data']
    all_hashtags = category_data.get('hashtags', [])
    cat_key = data['category_key']

    await callback.message.answer(
        "🏷 <b>Шаг 4/4 — Выберите хештеги для всех постов:</b>",
        reply_markup=hashtags_keyboard(all_hashtags, [], callback.from_user.id, cat_key),
        parse_mode='HTML'
    )
    await state.set_state(PostFlow.selecting_hashtags)


@router.callback_query(F.data == "batch:confirm_publish")
async def batch_publish_confirmed(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    posts = data.get('posts', [])
    schedule = data.get('schedule', [])
    category_key = data.get('category_key')
    topic = data.get('topic')

    if not posts or not schedule:
        await callback.answer("❌ Ошибка данных", show_alert=True)
        return

    if queue_mgr.running and queue_mgr.is_batch_active_for_category(category_key=category_key, topic=topic):
        active_info = queue_mgr.get_active_info()
        active_name = active_info.get("name", "текущий пакет") if active_info else "текущий пакет"
        active_progress = queue_mgr._active_progress or ""

        category_data = data.get('category_data', {})
        category_name = f"{category_data.get('emoji', '')} {category_data.get('name', 'эта категория')}"

        await callback.message.edit_text(
            f"⚠️ <b>В категорию {category_name} уже идёт пакетная отправка!</b>\n\n"
            f"▶️ <b>{active_name}</b>"
            + (f"\n📊 Прогресс: <i>{active_progress}</i>" if active_progress else "") +
            f"\n\nКак добавить новый пакет <b>({len(posts)} постов)</b>?\n\n"
            f"🔀 <b>Совместить</b> — посты будут чередоваться\n"
            f"📅 <b>После</b> — новый пакет начнётся после текущего",
            reply_markup=batch_conflict_keyboard(),
            parse_mode='HTML'
        )
        return

    await _do_add_batch_to_queue(callback, state, mode="after")


@router.callback_query(F.data.startswith("batch_conflict:"))
async def batch_conflict_choice(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    action = callback.data.split(":")[1]
    if action == "cancel":
        await callback.message.edit_text("❌ Добавление пакета отменено")
        await callback.message.answer("Что дальше?", reply_markup=after_post_keyboard())
        await state.clear()
        return
    await _do_add_batch_to_queue(callback, state, mode=action)


async def _do_add_batch_to_queue(callback: CallbackQuery, state: FSMContext, mode: str):
    data = await state.get_data()

    posts = data.get('posts', [])
    category_data = data['category_data']
    category_key = data['category_key']
    platform_id = data['platform_id']
    selected_hashtags = data.get('selected_hashtags', [])
    schedule = data.get('schedule', [])
    topic = data.get('topic')
    time_category = data.get('time_category', 'minute')
    time_value = data.get('time_value', 60)
    shuffle = data.get('shuffle', False)
    stars_price = data.get('stars_price')

    await callback.message.edit_text("⏳ Запуск пакетной публикации...")

    posts_formatted = [[(mtype, fid) for mtype, fid, *_ in post] for post in posts]

    # Нормализуем schedule в timezone-aware datetime
    schedule_dt = []
    for s in schedule:
        if isinstance(s, str):
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = pytz.timezone('Europe/Kiev').localize(dt)
                schedule_dt.append(dt)
            except Exception:
                schedule_dt.append(s)
        elif isinstance(s, datetime):
            if s.tzinfo is None:
                s = pytz.timezone('Europe/Kiev').localize(s)
            schedule_dt.append(s)
        else:
            schedule_dt.append(s)

    batch_id = queue_mgr.add_batch_to_queue(
        user_id=callback.from_user.id,
        category_data=category_data, category_key=category_key,
        platform_id=platform_id, posts=posts_formatted,
        selected_hashtags=selected_hashtags, schedule=schedule_dt,
        topic=topic, time_period=time_category, time_value=time_value, shuffle=shuffle,
        selected_dates=data.get('selected_dates', []),
        selected_times=data.get('selected_times', []),
        stars_price=stars_price,
    )

    media_proc = MediaProcessor(callback.message.bot)
    if not queue_mgr.running:
        await queue_mgr.start_queue(media_proc, callback.message.bot)
        mode_emoji = "▶️ Запущен и публикуется"
    else:
        mode_emoji = "✅ Добавлен, публикуется параллельно"

    first_dt = schedule_dt[0] if schedule_dt else datetime.now()
    last_dt = schedule_dt[-1] if len(schedule_dt) > 1 else first_dt

    stars_info = f"\n⭐ Цена за пост: <b>{stars_price} звёзд</b>" if stars_price else ""

    await callback.message.edit_text(
        f"✅ <b>Пакет запущен!</b>\n\n"
        f"📦 ID: <b>#{batch_id[:4]}</b>\n"
        f"📊 Постов: <b>{len(posts)}</b>\n"
        f"🔀 Перемешан: <b>{'Да' if shuffle else 'Нет'}</b>\n"
        f"{stars_info}\n"
        f"{mode_emoji}\n\n"
        f"🕐 Первый: <b>{first_dt.strftime('%d.%m %H:%M')}</b>\n"
        f"🕐 Последний: <b>{last_dt.strftime('%d.%m %H:%M')}</b>",
        parse_mode='HTML'
    )
    await callback.message.answer("Что дальше?", reply_markup=after_post_keyboard())
    await state.clear()


# ============ МОИ ХЕШТЕГИ ============

@router.message(F.text == "#️⃣ Мои хештеги")
async def my_hashtags_menu(message: Message, state: FSMContext):
    await message.answer(
        "#️⃣ <b>Управление хештегами</b>",
        reply_markup=my_hashtags_menu_keyboard(message.from_user.id),
        parse_mode='HTML'
    )


@router.callback_query(F.data.startswith("myhash:"))
async def my_hashtags_actions(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]

    if action == "back":
        await callback.message.edit_text("👋 Главное меню")
        await callback.message.answer("Выберите действие:", reply_markup=main_menu())
        return

    if action == "menu":
        await callback.message.edit_text(
            "#️⃣ <b>Управление хештегами</b>",
            reply_markup=my_hashtags_menu_keyboard(callback.from_user.id),
            parse_mode='HTML'
        )
        return

    if action == "help":
        await callback.message.edit_text(
            "ℹ️ <b>Как использовать хештеги</b>\n\n"
            "Шаблоны — сохранённые наборы для быстрого применения.\n"
            "Создать: при выборе хештегов нажмите 💾 Сохранить как шаблон.\n"
            "Использовать: нажмите 📋 Мои шаблоны при выборе хештегов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="myhash:menu")]
            ]),
            parse_mode='HTML'
        )
        return

    if action == "templates":
        await callback.message.edit_text(
            "📋 <b>Мои шаблоны хештегов</b>\n\nВыберите категорию:",
            reply_markup=user_templates_list_keyboard(callback.from_user.id),
            parse_mode='HTML'
        )
        return

    if action == "cat":
        category_key = parts[2]
        await callback.message.edit_text(
            "📋 <b>Шаблоны категории</b>\n\nВыберите шаблон:",
            reply_markup=category_templates_keyboard(callback.from_user.id, category_key),
            parse_mode='HTML'
        )
        return

    if action == "view":
        category_key = parts[2]
        template_name = ":".join(parts[3:])

        config = load_config()
        user_str = str(callback.from_user.id)
        template_tags = config.get('hashtag_templates', {}).get(user_str, {}).get(category_key, {}).get(template_name, [])

        tags_text = '\n'.join(template_tags)

        await callback.message.edit_text(
            f"📋 <b>Шаблон: {template_name}</b>\n\n"
            f"📊 Хештегов: {len(template_tags)}\n\n{tags_text}",
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
                    await callback.message.edit_text(
                        "📋 <b>Шаблоны категории</b>",
                        reply_markup=category_templates_keyboard(callback.from_user.id, category_key),
                        parse_mode='HTML'
                    )
                    return

        await callback.answer("❌ Шаблон не найден", show_alert=True)
        return


# ============ ЧЕРНОВИКИ ============

@router.message(F.text == "💾 Черновики")
async def show_drafts(message: Message, state: FSMContext):
    user_drafts = draft_mgr.get_user_drafts(message.from_user.id)

    if not user_drafts:
        await message.answer(
            "📂 <b>Черновики пусты</b>",
            reply_markup=main_menu(),
            parse_mode='HTML'
        )
        return

    await message.answer(
        f"📂 <b>Ваши черновики ({len(user_drafts)})</b>\n\nВыберите черновик:",
        reply_markup=drafts_list_keyboard(user_drafts),
        parse_mode='HTML'
    )


@router.callback_query(F.data.startswith("dr:"))
async def draft_actions(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    action = parts[1]

    if action not in ("pub", "toq"):
        await callback.answer()

    if action == "back":
        await callback.message.edit_text("👋 Главное меню")
        await callback.message.answer("Выберите действие:", reply_markup=main_menu())
        return

    if action == "list":
        user_drafts = draft_mgr.get_user_drafts(callback.from_user.id)
        if user_drafts:
            await callback.message.edit_text(
                f"📂 <b>Ваши черновики ({len(user_drafts)})</b>",
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
        await callback.message.edit_text(info, reply_markup=draft_action_keyboard(draft_id), parse_mode='HTML')
        return

    if action == "pub":
        draft_id = parts[2]
        draft = draft_mgr.get_draft(draft_id)

        if not draft:
            await callback.answer("❌ Черновик не найден", show_alert=True)
            return

        await callback.answer("📤 Публикуем...")

        is_comic = draft.get('is_comic', False)

        if is_comic:
            caption = format_comic_post(
                draft['category_data'], draft.get('comic_title', ''),
                draft.get('comic_link', ''), draft['selected_hashtags'], draft['platform_id']
            )

            try:
                chat_id, thread_id = resolve_chat_and_thread(draft['platform_id'], draft.get('topic'))
            except Exception as e:
                await callback.message.edit_text(f"❌ Ошибка: не найдена тема.\n\n{e}")
                return

            try:
                kwargs = dict(chat_id=chat_id, text=caption, parse_mode='HTML')
                if thread_id is not None:
                    kwargs["message_thread_id"] = thread_id
                await callback.bot.send_message(**kwargs)
                draft_mgr.delete_draft(draft_id)
                await callback.message.edit_text("✅ Комикс опубликован и удален из черновиков!")
                await callback.message.answer("Что дальше?", reply_markup=main_menu())
            except Exception as e:
                await callback.message.edit_text(f"❌ Ошибка публикации: {str(e)}")
            return

        processor = MediaProcessor(callback.message.bot)
        media_files = draft['media_files']
        media_files.sort(key=lambda x: x[3] if len(x) > 3 else 0)
        media_to_process = [(mtype, fid) for mtype, fid, *_ in media_files]
        processed_media = await processor.process_batch(media_to_process)

        if not processed_media:
            await callback.message.edit_text("❌ Ошибка обработки файлов")
            return

        try:
            chat_id, thread_id = resolve_chat_and_thread(draft['platform_id'], draft.get('topic'))
        except Exception as e:
            await callback.message.edit_text(f"❌ Ошибка: не найдена тема.\n\n{e}")
            return

        caption = format_post_caption(
            draft['category_data'], draft['selected_hashtags'],
            draft['platform_id'], draft.get('description', '')
        )

        success = False
        if len(processed_media) == 1:
            success = await processor.send_single_media(
                chat_id, processed_media[0][0], processed_media[0][1], caption, thread_id
            )
        else:
            success = await processor.send_media_group(chat_id, processed_media, caption, thread_id)

        processor.cleanup_specific([p for _, p in processed_media])

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
                f"📂 <b>Ваши черновики ({len(user_drafts)})</b>",
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
    user_id = message.from_user.id
    status = queue_mgr.get_status(user_id)

    await message.answer(
        queue_mgr.get_status_text(user_id),
        reply_markup=queue_menu_keyboard(
            running=status['running'],
            count=status['pending'],
            interval=status['interval'],
            has_running_batches=bool(status.get('running_batches'))
        ),
        parse_mode='HTML'
    )


@router.callback_query(F.data.startswith("q:"))
async def queue_callback(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    user_id = callback.from_user.id

    if action not in ("start", "stop", "rm"):
        await callback.answer()

    if action == "back":
        await callback.message.edit_text("👋 Главное меню")
        await callback.message.answer("Выберите действие:", reply_markup=main_menu())
        return

    if action == "menu":
        status = queue_mgr.get_status(user_id)
        await callback.message.edit_text(
            queue_mgr.get_status_text(user_id),
            reply_markup=queue_menu_keyboard(
                running=status['running'],
                count=status['pending'],
                interval=status['interval'],
                has_running_batches=bool(status.get('running_batches'))
            ),
            parse_mode='HTML'
        )
        return

    if action == "start":
        processor = MediaProcessor(callback.message.bot)
        success = await queue_mgr.start_queue(processor, callback.message.bot)

        if success:
            await callback.answer("✅ Очередь запущена")
            status = queue_mgr.get_status(user_id)
            await callback.message.edit_text(
                queue_mgr.get_status_text(user_id),
                reply_markup=queue_menu_keyboard(
                    running=status['running'],
                    count=status['pending'],
                    interval=status['interval'],
                    has_running_batches=bool(status.get('running_batches'))
                ),
                parse_mode='HTML'
            )
        else:
            await callback.answer("❌ Нет постов для публикации", show_alert=True)
        return

    if action == "stop":
        await queue_mgr.stop_queue()
        await callback.answer("⏹ Остановлено")
        status = queue_mgr.get_status(user_id)
        await callback.message.edit_text(
            queue_mgr.get_status_text(user_id),
            reply_markup=queue_menu_keyboard(
                running=status['running'],
                count=status['pending'],
                interval=status['interval'],
                has_running_batches=bool(status.get('running_batches'))
            ),
            parse_mode='HTML'
        )
        return

    if action == "setint":
        await callback.message.answer("⏱ <b>Настройка интервала</b>\n\nВведите интервал в минутах (1-1440):",
                                      parse_mode='HTML')
        await state.set_state(QueueFlow.set_interval)
        return

    if action == "progress":
        progress_text = queue_mgr.get_all_progress_text(user_id)
        await callback.message.edit_text(
            f"📊 <b>Прогресс активных публикаций</b>\n\n{progress_text}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="q:progress")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="q:menu")],
            ]),
            parse_mode='HTML'
        )
        return

    if action == "batch_progress":
        batch_id = callback.data.split(":")[2]
        detail_text = queue_mgr.get_running_batch_detail_text(batch_id)
        await callback.message.edit_text(
            detail_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"q:batch_progress:{batch_id}")],
                [InlineKeyboardButton(text="🔙 К списку", callback_data="q:list")],
            ]),
            parse_mode='HTML'
        )
        return

    if action == "list":
        all_items = queue_mgr.get_queue(user_id)
        pending_items = queue_mgr.get_pending_queue(user_id)

        if not all_items:
            await callback.answer("Очередь пуста", show_alert=True)
            return

        running_count = len(queue_mgr.get_running_batch_ids(user_id))
        pending_count = len(pending_items)
        total_label = f"{running_count} активных + {pending_count} в очереди" if running_count else f"{pending_count} эл."

        await callback.message.edit_text(
            f"📋 <b>Очередь ({total_label})</b>",
            reply_markup=queue_list_keyboard(all_items, running=queue_mgr.running),
            parse_mode='HTML'
        )
        return

    if action == "detail":
        index = int(callback.data.split(":")[2])
        items = queue_mgr.get_pending_queue(user_id)
        if index >= len(items):
            await callback.answer("❌ Элемент не найден", show_alert=True)
            return

        item = items[index]
        is_active = queue_mgr.running and index == 0 and queue_mgr._active_id == item.get("id")

        item_type = item.get("type", "single")
        cat_data = item.get("category_data", {})
        cat_name = f"{cat_data.get('emoji', '')} {cat_data.get('name', '—')}"
        hashtags = item.get("selected_hashtags", [])
        stars_price = item.get("stars_price")

        if item_type == "batch":
            posts = item.get("posts", [])
            schedule = item.get("schedule", [])
            shuffle = item.get("shuffle", False)
            time_period = item.get("time_period", "—")
            time_value = item.get("time_value", "—")

            period_map = {"minute": "мин", "hour": "ч", "day": "дн", "week": "нед", "custom": "дней (график)"}
            period_label = period_map.get(time_period, time_period)

            first_str = last_str = "—"
            if schedule:
                def parse_dt(s):
                    if isinstance(s, str):
                        dt = datetime.fromisoformat(s)
                    else:
                        dt = s
                    if dt.tzinfo is None:
                        import pytz as _pytz
                        dt = _pytz.timezone('Europe/Kiev').localize(dt)
                    return dt.astimezone(pytz.timezone('Europe/Kiev'))

                first_str = parse_dt(schedule[0]).strftime("%d.%m %H:%M")
                last_str = parse_dt(schedule[-1]).strftime("%d.%m %H:%M") if len(schedule) > 1 else first_str

            files_per_post = len(posts[0]) if posts else 0
            status = "▶️ <b>Публикуется прямо сейчас</b>\n" if is_active else ""
            stars_info = f"\n⭐ Цена за пост: <b>{stars_price} звёзд</b>" if stars_price else ""

            text = (
                f"📦 <b>{item.get('name', 'Пакет')}</b>\n\n"
                f"{status}"
                f"📁 Категория: {cat_name}\n"
                f"📊 Постов: <b>{len(posts)}</b>\n"
                f"🖼 Файлов в посте: <b>{files_per_post}</b>\n"
                f"🔀 Перемешан: <b>{'Да' if shuffle else 'Нет'}</b>\n"
                f"⏰ Период: <b>{time_value} {period_label}</b>\n"
                f"{stars_info}\n"
                f"🕐 Первый: <b>{first_str}</b>\n"
                f"🕐 Последний: <b>{last_str}</b>\n"
                f"#️⃣ Хештегов: <b>{len(hashtags)}</b>\n"
            )
        else:
            media_files = item.get("media_files", [])
            text = (
                f"📄 <b>{item.get('name', 'Пост')}</b>\n\n"
                f"{'▶️ <b>Публикуется прямо сейчас</b>' if is_active else ''}"
                f"\n📁 Категория: {cat_name}\n"
                f"🖼 Файлов: <b>{len(media_files)}</b>\n"
                f"#️⃣ Хештегов: <b>{len(hashtags)}</b>\n"
            )

        await callback.message.edit_text(text, reply_markup=queue_item_detail_keyboard(index, is_active),
                                         parse_mode='HTML')
        return

    if action == "rm":
        index = int(callback.data.split(":")[2])
        removed = queue_mgr.remove_from_queue(index, user_id)
        if not removed:
            await callback.answer("⚠️ Нельзя удалить — сейчас публикуется!", show_alert=True)
        else:
            await callback.answer("🗑️ Удалено")

        all_items = queue_mgr.get_queue(user_id)
        pending_items = queue_mgr.get_pending_queue(user_id)

        if all_items:
            running_count = len(queue_mgr.get_running_batch_ids(user_id))
            pending_count = len(pending_items)
            total_label = f"{running_count} активных + {pending_count} в очереди" if running_count else f"{pending_count} эл."
            await callback.message.edit_text(
                f"📋 <b>Очередь ({total_label})</b>",
                reply_markup=queue_list_keyboard(all_items, running=queue_mgr.running),
                parse_mode='HTML'
            )
        else:
            await callback.message.edit_text("📋 Очередь пуста")
            await callback.message.answer("Что дальше?", reply_markup=main_menu())
        return

    if action == "clear":
        queue_mgr.clear_queue(user_id)
        await callback.answer("🗑️ Очередь очищена")
        status = queue_mgr.get_status(user_id)
        await callback.message.edit_text(
            queue_mgr.get_status_text(user_id),
            reply_markup=queue_menu_keyboard(
                running=status['running'],
                count=status['pending'],
                interval=status['interval'],
                has_running_batches=bool(status.get('running_batches'))
            ),
            parse_mode='HTML'
        )
        return


@router.callback_query(F.data == "q:noop")
async def queue_noop(callback: CallbackQuery):
    await callback.answer("▶️ Этот элемент сейчас публикуется — удаление невозможно", show_alert=True)


@router.message(QueueFlow.set_interval)
async def queue_interval_set(message: Message, state: FSMContext):
    try:
        interval = int(message.text)
        if 1 <= interval <= 1440:
            queue_mgr.set_interval(interval)
            await message.answer(f"✅ Интервал установлен: {interval} минут", reply_markup=main_menu())
            await state.clear()
        else:
            await message.answer("❌ Введите число от 1 до 1440")
    except ValueError:
        await message.answer("❌ Введите корректное число")


# ============ НАСТРОЙКИ ============

@router.message(F.text == "⚙️ Настройки")
async def settings_menu(message: Message, state: FSMContext):
    config = load_config()
    desc_enabled = config.get('features', {}).get('post_description', False)
    desc_status = "✅ Вкл" if desc_enabled else "❌ Выкл"

    await message.answer(
        f"⚙️ <b>Настройки</b>\n\n"
        f"📝 Описание поста: <b>{desc_status}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"📝 Описание постов: {desc_status}",
                callback_data="settings:toggle_description"
            )],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="settings:back")],
        ]),
        parse_mode='HTML'
    )


@router.callback_query(F.data.startswith("settings:"))
async def settings_callback(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]

    if action == "toggle_description":
        config = load_config()
        current = config.get('features', {}).get('post_description', False)
        if 'features' not in config:
            config['features'] = {}
        config['features']['post_description'] = not current
        save_config(config)

        new_val = not current
        desc_status = "✅ Вкл" if new_val else "❌ Выкл"
        await callback.answer(f"📝 Описание постов: {desc_status}")

        await callback.message.edit_text(
            f"⚙️ <b>Настройки</b>\n\n"
            f"📝 Описание поста: <b>{desc_status}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"📝 Описание постов: {desc_status}",
                    callback_data="settings:toggle_description"
                )],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="settings:back")],
            ]),
            parse_mode='HTML'
        )
        return

    if action == "back":
        await callback.message.delete()
        await callback.message.answer("Выберите действие:", reply_markup=main_menu())
        return


# ============ ОЧИСТКА ЧАТА ============

@router.message(F.text == "🧹 Очистить чат")
async def clear_chat(message: Message, bot: Bot):
    try:
        for i in range(1, 100):
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id - i)
            except Exception:
                pass
        await message.answer("✅ Чат очищен", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка очистки: {e}")
