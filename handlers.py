"""
handlers.py — все обработчики Telegram-бота (aiogram 3.x).

Архитектура:
- Один глобальный Router
- Синглтоны MediaProcessor, QueueManager, DraftManager, BatchPostingService
  создаются при первом обращении через get_*() хелперы
- FSM через PostFlow / HashtagFlow / QueueFlow из states.py
"""

import json
import logging
from pathlib import Path
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    Message,
)

from batch_service import BatchPostingService
from draft_manager import DraftManager
from keyboards import (
    after_post_keyboard,
    batch_schedule_keyboard,
    categories_keyboard,
    category_templates_keyboard,
    comic_confirm_keyboard,
    comic_input_keyboard,
    confirm_batch_final_keyboard,
    draft_action_keyboard,
    drafts_list_keyboard,
    files_per_post_keyboard,
    hashtag_templates_keyboard,
    hashtags_keyboard,
    main_menu,
    media_upload_keyboard,
    my_hashtags_menu_keyboard,
    queue_list_keyboard,
    queue_menu_keyboard,
    template_actions_keyboard,
    time_options_keyboard,
    user_templates_list_keyboard,
)
from media_processor import MediaProcessor
from queue_manager import QueueManager
from states import HashtagFlow, PostFlow, QueueFlow

logger = logging.getLogger(__name__)
router = Router()

# ──────────────────────────────────────────────────────────────────────────────
#  Конфиг
# ──────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────────────────────────────────────
#  Синглтоны (инициализируются лениво с реальным bot-объектом)
# ──────────────────────────────────────────────────────────────────────────────

_media_processor: Optional[MediaProcessor] = None
_queue_manager: Optional[QueueManager] = None
_draft_manager: Optional[DraftManager] = None
_batch_service: Optional[BatchPostingService] = None


def get_media_processor(bot: Bot) -> MediaProcessor:
    global _media_processor
    if _media_processor is None:
        _media_processor = MediaProcessor(bot=bot)
    return _media_processor


def get_queue_manager() -> QueueManager:
    global _queue_manager
    if _queue_manager is None:
        _queue_manager = QueueManager()
    return _queue_manager


def get_draft_manager() -> DraftManager:
    global _draft_manager
    if _draft_manager is None:
        _draft_manager = DraftManager()
    return _draft_manager


def get_batch_service(bot: Bot) -> BatchPostingService:
    global _batch_service
    if _batch_service is None:
        _batch_service = BatchPostingService(
            media_processor=get_media_processor(bot), bot=bot
        )
    return _batch_service


# ──────────────────────────────────────────────────────────────────────────────
#  Вспомогательные функции
# ──────────────────────────────────────────────────────────────────────────────

def _get_user_category_data(user_id: int, category_name_with_emoji: str) -> Optional[dict]:
    """Ищет категорию пользователя по отображаемому имени (с эмодзи)."""
    config = load_config()
    for participant in config["participants"].values():
        if participant.get("user_id") != user_id:
            continue
        for cat_key, cat_data in participant["categories"].items():
            display = f"{cat_data.get('emoji', '')} {cat_data['name']}"
            if display == category_name_with_emoji:
                return {"key": cat_key, **cat_data}
    return None


def _get_user_category_by_key(user_id: int, category_key: str) -> Optional[dict]:
    config = load_config()
    for participant in config["participants"].values():
        if participant.get("user_id") != user_id:
            continue
        cat_data = participant["categories"].get(category_key)
        if cat_data:
            return {"key": category_key, **cat_data}
    return None


def _is_authorized(user_id: int) -> bool:
    config = load_config()
    return any(
        p.get("user_id") == user_id for p in config["participants"].values()
    )


async def _safe_delete(message: Message):
    """Удаляет сообщение, игнорируя ошибки."""
    try:
        await message.delete()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
#  /start  и главное меню
# ──────────────────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if not _is_authorized(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    await state.clear()
    await message.answer(
        "👋 Привет! Выберите действие:",
        reply_markup=main_menu(),
    )


@router.message(F.text == "🏠 Главная")
async def go_home(message: Message, state: FSMContext):
    if not _is_authorized(message.from_user.id):
        return
    await state.clear()
    await message.answer("🏠 Главное меню", reply_markup=main_menu())


# ──────────────────────────────────────────────────────────────────────────────
#  Очистка чата
# ──────────────────────────────────────────────────────────────────────────────

@router.message(F.text == "🧹 Очистить чат")
async def clear_chat(message: Message):
    if not _is_authorized(message.from_user.id):
        return
    config = load_config()
    limit = config.get("posting", {}).get("clear_chat_limit", 100)
    deleted = 0
    msg_id = message.message_id
    for i in range(msg_id, max(msg_id - limit, 0), -1):
        try:
            await message.bot.delete_message(message.chat.id, i)
            deleted += 1
        except Exception:
            pass
    await message.answer(f"🧹 Удалено сообщений: {deleted}", reply_markup=main_menu())


# ──────────────────────────────────────────────────────────────────────────────
#  СОЗДАНИЕ ПОСТА — выбор категории
# ──────────────────────────────────────────────────────────────────────────────

@router.message(F.text == "➕ Создать пост")
async def create_post_start(message: Message, state: FSMContext):
    if not _is_authorized(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "📁 Выберите категорию:",
        reply_markup=categories_keyboard(message.from_user.id),
    )
    await state.set_state(PostFlow.selecting_category)


@router.message(F.text == "➕ Ещё пост")
async def another_post(message: Message, state: FSMContext):
    await create_post_start(message, state)


@router.message(PostFlow.selecting_category)
async def category_selected(message: Message, state: FSMContext):
    if message.text in ("❌ Отмена", "🏠 Главная"):
        await go_home(message, state)
        return

    cat = _get_user_category_data(message.from_user.id, message.text)
    if not cat:
        await message.answer("❌ Категория не найдена. Попробуйте ещё раз.")
        return

    # Режим ссылки-поста (комикс)
    if cat.get("mode") == "link_post":
        await state.update_data(
            category_key=cat["key"],
            category_data=cat,
            platform_id=cat.get("platform", "main_channel"),
            topic=cat.get("topic"),
        )
        await message.answer(
            f"🔗 Режим «Комикс»\n\nВведите ссылку на комикс:",
            reply_markup=comic_input_keyboard(),
        )
        await state.set_state(PostFlow.comic_link)
        return

    await state.update_data(
        category_key=cat["key"],
        category_data=cat,
        platform_id=cat.get("platform", "main_channel"),
        topic=cat.get("topic"),
        media_files=[],
    )
    await message.answer(
        f"📸 Загружайте фото/видео для категории «{cat['name']}».\n"
        "Нажмите «Далее» когда закончите.",
        reply_markup=media_upload_keyboard(0),
    )
    await state.set_state(PostFlow.uploading_media)


# ──────────────────────────────────────────────────────────────────────────────
#  ЗАГРУЗКА МЕДИА
# ──────────────────────────────────────────────────────────────────────────────

@router.message(PostFlow.uploading_media, F.photo | F.video)
async def media_received(message: Message, state: FSMContext):
    data = await state.get_data()
    media_files = data.get("media_files", [])

    if message.photo:
        file_id = message.photo[-1].file_id
        media_files.append(("photo", file_id))
    elif message.video:
        media_files.append(("video", message.video.file_id))

    await state.update_data(media_files=media_files)
    count = len(media_files)

    config = load_config()
    threshold = config.get("posting", {}).get("upload_feedback_threshold", 5)
    if count % threshold == 0 or count == 1:
        await message.answer(
            f"✅ Загружено файлов: {count}",
            reply_markup=media_upload_keyboard(count),
        )


@router.message(PostFlow.uploading_media, F.text.startswith("➡️ Далее"))
async def media_next(message: Message, state: FSMContext):
    data = await state.get_data()
    media_files = data.get("media_files", [])

    if not media_files:
        await message.answer("⚠️ Загрузите хотя бы один файл.")
        return

    cat = data["category_data"]
    await message.answer(
        "🏷 Выберите хештеги:",
        reply_markup=hashtags_keyboard(
            hashtags=cat.get("hashtags", []),
            selected=[],
            user_id=message.from_user.id,
            category_key=data["category_key"],
        ),
    )
    await state.set_state(PostFlow.selecting_hashtags)


@router.message(PostFlow.uploading_media, F.text == "💾 В черновик")
async def save_to_draft_from_upload(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("media_files"):
        await message.answer("⚠️ Нет файлов для сохранения.")
        return

    dm = get_draft_manager()
    cat = data["category_data"]
    draft_id = dm.create_draft(
        user_id=message.from_user.id,
        name=f"{cat['name']} — черновик",
        category_key=data["category_key"],
        category_data=cat,
        platform_id=data["platform_id"],
        media_files=data["media_files"],
        selected_hashtags=[],
        topic=data.get("topic"),
    )
    await state.clear()
    await message.answer(
        f"💾 Черновик сохранён (ID: {draft_id})",
        reply_markup=main_menu(),
    )


@router.message(PostFlow.uploading_media, F.text == "📥 В очередь")
async def add_to_queue_from_upload(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("media_files"):
        await message.answer("⚠️ Нет файлов для добавления.")
        return

    qm = get_queue_manager()
    cat = data["category_data"]
    qm.add_to_queue(
        user_id=message.from_user.id,
        name=f"{cat['name']}",
        category_key=data["category_key"],
        category_data=cat,
        platform_id=data["platform_id"],
        media_files=data["media_files"],
        selected_hashtags=[],
        topic=data.get("topic"),
    )
    await state.clear()
    await message.answer(
        f"📥 Добавлено в очередь ({len(data['media_files'])} файлов)",
        reply_markup=main_menu(),
    )


@router.message(PostFlow.uploading_media, F.text == "🗑️ Очистить всё")
async def clear_media(message: Message, state: FSMContext):
    await state.update_data(media_files=[])
    await message.answer("🗑️ Все файлы удалены.", reply_markup=media_upload_keyboard(0))


@router.message(PostFlow.uploading_media, F.text == "❌ Отмена")
async def cancel_upload(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=main_menu())


# ──────────────────────────────────────────────────────────────────────────────
#  КОМИКСЫ
# ──────────────────────────────────────────────────────────────────────────────

@router.message(PostFlow.comic_link)
async def comic_link_received(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=main_menu())
        return

    link = message.text.strip()
    if not link.startswith("http"):
        await message.answer("⚠️ Введите корректную ссылку (начинается с http).")
        return

    await state.update_data(comic_link=link)
    await message.answer(
        "📝 Теперь введите название комикса:",
        reply_markup=comic_input_keyboard(),
    )
    await state.set_state(PostFlow.comic_title)


@router.message(PostFlow.comic_title)
async def comic_title_received(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=main_menu())
        return

    title = message.text.strip()
    await state.update_data(comic_title=title)
    data = await state.get_data()

    preview = (
        f"🔗 <b>Комикс готов к публикации</b>\n\n"
        f"📖 Название: {title}\n"
        f"🔗 Ссылка: {data['comic_link']}\n\n"
        f"Выберите действие:"
    )
    await message.answer(preview, reply_markup=comic_confirm_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == "comic:publish")
async def comic_publish(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mp = get_media_processor(callback.bot)
    config = load_config()

    cat = data["category_data"]
    platform_id = data["platform_id"]
    chat_id = config["platforms"][platform_id]["chat_id"]
    thread_id = None
    if data.get("topic"):
        thread_id = (
            config["platforms"][platform_id]["topics"]
            .get(data["topic"], {})
            .get("thread_id")
        )

    invite_link = config["platforms"][platform_id].get("invite_link", "")
    emoji_row = cat.get("emoji_row", "")
    caption = (
        f"{cat['emoji']} {cat['name'].upper()} {cat['emoji']}\n\n"
        f"{emoji_row}\n\n"
        f'📖 <b><u><a href="{data["comic_link"]}">{data["comic_title"]}</a></u></b>\n\n'
        f"{emoji_row}\n\n"
        f"↘️⬇️↙️\n"
        f'<a href="{invite_link}">Ссылка для друга</a>'
    )

    kwargs = dict(chat_id=chat_id, text=caption, parse_mode="HTML", disable_web_page_preview=False)
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    await callback.bot.send_message(**kwargs)

    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("✅ Комикс опубликован!", reply_markup=after_post_keyboard())
    await callback.answer()


@router.callback_query(F.data == "comic:cancel")
async def comic_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("❌ Отменено.", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "comic:edit_title")
async def comic_edit_title(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Введите новое название:", reply_markup=comic_input_keyboard())
    await state.set_state(PostFlow.comic_title)
    await callback.answer()


@router.callback_query(F.data == "comic:edit_link")
async def comic_edit_link(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🔗 Введите новую ссылку:", reply_markup=comic_input_keyboard())
    await state.set_state(PostFlow.comic_link)
    await callback.answer()


@router.callback_query(F.data == "comic:add_hashtags")
async def comic_add_hashtags(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cat = data["category_data"]
    await callback.message.answer(
        "🏷 Выберите хештеги для комикса:",
        reply_markup=hashtags_keyboard(
            hashtags=cat.get("hashtags", []),
            selected=data.get("selected_hashtags", []),
            user_id=callback.from_user.id,
            category_key=data["category_key"],
        ),
    )
    await state.update_data(is_comic=True)
    await state.set_state(PostFlow.comic_hashtags)
    await callback.answer()


# ──────────────────────────────────────────────────────────────────────────────
#  ХЕШТЕГИ — выбор
# ──────────────────────────────────────────────────────────────────────────────

async def _hashtag_toggle(callback: CallbackQuery, state: FSMContext):
    """Общая логика переключения хештега."""
    tag = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected = data.get("selected_hashtags", [])

    if tag == "select_all":
        cat = data["category_data"]
        selected = list(cat.get("hashtags", []))
    elif tag == "clear_all":
        selected = []
    elif tag in selected:
        selected.remove(tag)
    else:
        selected.append(tag)

    await state.update_data(selected_hashtags=selected)
    cat = data["category_data"]
    await callback.message.edit_reply_markup(
        reply_markup=hashtags_keyboard(
            hashtags=cat.get("hashtags", []),
            selected=selected,
            user_id=callback.from_user.id,
            category_key=data["category_key"],
        )
    )
    await callback.answer()


@router.callback_query(PostFlow.selecting_hashtags, F.data.startswith("tag:"))
async def hashtag_callback_post(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]

    if action == "done":
        await _publish_post_flow(callback, state)
        return
    if action == "templates":
        data = await state.get_data()
        await callback.message.edit_reply_markup(
            reply_markup=hashtag_templates_keyboard(
                callback.from_user.id,
                data["category_key"],
                data.get("selected_hashtags", []),
            )
        )
        await callback.answer()
        return
    if action == "back":
        data = await state.get_data()
        cat = data["category_data"]
        await callback.message.edit_reply_markup(
            reply_markup=hashtags_keyboard(
                cat.get("hashtags", []),
                data.get("selected_hashtags", []),
                callback.from_user.id,
                data["category_key"],
            )
        )
        await callback.answer()
        return
    if action == "add_custom":
        await callback.message.answer("✏️ Введите свой хештег (с #):")
        await state.set_state(HashtagFlow.adding_custom)
        await callback.answer()
        return
    if action == "save_template":
        await callback.message.answer("💾 Введите название шаблона:")
        await state.set_state(HashtagFlow.naming_template)
        await callback.answer()
        return

    await _hashtag_toggle(callback, state)


@router.callback_query(PostFlow.comic_hashtags, F.data.startswith("tag:"))
async def hashtag_callback_comic(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 1)[1]
    if action == "done":
        # Вернуться к подтверждению комикса
        data = await state.get_data()
        await callback.message.answer(
            "✅ Хештеги добавлены. Публикуем комикс?",
            reply_markup=comic_confirm_keyboard(),
        )
        await callback.answer()
        return
    await _hashtag_toggle(callback, state)


@router.callback_query(F.data.startswith("tmpl:apply:"))
async def apply_template(callback: CallbackQuery, state: FSMContext):
    template_name = callback.data.split(":", 2)[2]
    data = await state.get_data()
    config = load_config()
    user_str = str(callback.from_user.id)
    cat_key = data["category_key"]
    tags = (
        config.get("hashtag_templates", {})
        .get(user_str, {})
        .get(cat_key, {})
        .get(template_name, [])
    )
    await state.update_data(selected_hashtags=tags)
    cat = data["category_data"]
    await callback.message.edit_reply_markup(
        reply_markup=hashtags_keyboard(
            cat.get("hashtags", []),
            tags,
            callback.from_user.id,
            cat_key,
        )
    )
    await callback.answer(f"✅ Шаблон «{template_name}» применён")


# ──────────────────────────────────────────────────────────────────────────────
#  ХЕШТЕГИ — пользовательские / шаблоны
# ──────────────────────────────────────────────────────────────────────────────

@router.message(HashtagFlow.adding_custom)
async def custom_hashtag_input(message: Message, state: FSMContext):
    tag = message.text.strip()
    if not tag.startswith("#"):
        tag = "#" + tag

    data = await state.get_data()
    selected = data.get("selected_hashtags", [])
    if tag not in selected:
        selected.append(tag)
    await state.update_data(selected_hashtags=selected)

    # Добавить тег в конфиг пользователя
    config = load_config()
    user_str = str(message.from_user.id)
    cat_key = data["category_key"]
    for part_data in config["participants"].values():
        if part_data.get("user_id") == message.from_user.id:
            if tag not in part_data["categories"].get(cat_key, {}).get("hashtags", []):
                part_data["categories"][cat_key].setdefault("hashtags", []).append(tag)
            break
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    cat = data["category_data"]
    # Обновляем cat_data в state тоже
    cat.setdefault("hashtags", [])
    if tag not in cat["hashtags"]:
        cat["hashtags"].append(tag)
    await state.update_data(category_data=cat)

    await message.answer(
        f"✅ Хештег {tag} добавлен",
        reply_markup=hashtags_keyboard(
            cat.get("hashtags", []),
            selected,
            message.from_user.id,
            cat_key,
        ),
    )
    await state.set_state(PostFlow.selecting_hashtags)


@router.message(HashtagFlow.naming_template)
async def template_name_input(message: Message, state: FSMContext):
    name = message.text.strip()
    data = await state.get_data()
    selected = data.get("selected_hashtags", [])
    cat_key = data["category_key"]

    config = load_config()
    user_str = str(message.from_user.id)
    config.setdefault("hashtag_templates", {}).setdefault(user_str, {}).setdefault(
        cat_key, {}
    )[name] = selected
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    cat = data["category_data"]
    await message.answer(
        f"💾 Шаблон «{name}» сохранён ({len(selected)} тегов)",
        reply_markup=hashtags_keyboard(
            cat.get("hashtags", []),
            selected,
            message.from_user.id,
            cat_key,
        ),
    )
    await state.set_state(PostFlow.selecting_hashtags)


# ──────────────────────────────────────────────────────────────────────────────
#  ПУБЛИКАЦИЯ ПОСТА
# ──────────────────────────────────────────────────────────────────────────────

async def _publish_post_flow(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает нажатие «Готово» в выборе хештегов → публикует пост."""
    data = await state.get_data()
    selected = data.get("selected_hashtags", [])
    if not selected:
        await callback.answer("⚠️ Выберите хотя бы один хештег!", show_alert=True)
        return

    await callback.message.answer("⏳ Публикую пост...")
    await callback.answer()

    mp = get_media_processor(callback.bot)
    config = load_config()
    cat = data["category_data"]
    platform_id = data["platform_id"]
    chat_id = config["platforms"][platform_id]["chat_id"]
    thread_id = None
    if data.get("topic"):
        thread_id = (
            config["platforms"][platform_id]["topics"]
            .get(data["topic"], {})
            .get("thread_id")
        )

    processed = await mp.process_batch(data["media_files"])
    if not processed:
        await callback.message.answer("❌ Не удалось обработать медиафайлы.")
        return

    # Формируем caption
    emoji_row = cat.get("emoji_row", "")
    invite_link = config["platforms"][platform_id].get("invite_link", "")
    hashtag_lines = [
        " ".join(selected[i : i + 4]) for i in range(0, len(selected), 4)
    ]
    caption = (
        f"{cat['emoji']} {cat['name'].upper()} {cat['emoji']}\n\n"
        f"{emoji_row}\n"
        + "\n".join(hashtag_lines)
        + f"\n{emoji_row}\n\n"
        f"↘️⬇️↙️\n"
        f'<a href="{invite_link}">Ссылка для друга</a>'
    )

    if len(processed) == 1:
        mt, fp = processed[0]
        await mp.send_single_media(chat_id, mt, fp, caption, thread_id)
    else:
        await mp.send_media_group(chat_id, processed, caption, thread_id)

    mp.cleanup_specific([fp for _, fp in processed])

    await state.clear()
    await callback.message.answer("✅ Пост опубликован!", reply_markup=after_post_keyboard())


# ──────────────────────────────────────────────────────────────────────────────
#  ПАКЕТНАЯ ЗАГРУЗКА
# ──────────────────────────────────────────────────────────────────────────────

@router.message(F.text == "📦 Пакетная загрузка")
async def batch_start(message: Message, state: FSMContext):
    if not _is_authorized(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "📦 Режим пакетной загрузки\n\nВыберите категорию:",
        reply_markup=categories_keyboard(message.from_user.id),
    )
    await state.set_state(PostFlow.selecting_category)
    await state.update_data(batch_mode=True)


@router.message(PostFlow.batch_uploading, F.photo | F.video)
async def batch_media_received(message: Message, state: FSMContext):
    data = await state.get_data()
    media_files = data.get("media_files", [])

    if message.photo:
        media_files.append(("photo", message.photo[-1].file_id))
    elif message.video:
        media_files.append(("video", message.video.file_id))

    await state.update_data(media_files=media_files)
    count = len(media_files)

    config = load_config()
    threshold = config.get("posting", {}).get("upload_feedback_threshold", 5)
    if count % threshold == 0 or count == 1:
        await message.answer(
            f"✅ Загружено: {count} файлов",
            reply_markup=media_upload_keyboard(count),
        )


@router.message(PostFlow.batch_uploading, F.text.startswith("➡️ Далее"))
async def batch_next(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data.get("media_files"):
        await message.answer("⚠️ Загрузите хотя бы один файл.")
        return

    cat = data["category_data"]
    await message.answer(
        "🏷 Выберите хештеги для пакета:",
        reply_markup=hashtags_keyboard(
            cat.get("hashtags", []),
            [],
            message.from_user.id,
            data["category_key"],
        ),
    )
    await state.set_state(PostFlow.selecting_hashtags)
    await state.update_data(after_hashtags="batch_schedule")


@router.message(PostFlow.batch_uploading, F.text == "❌ Отмена")
async def batch_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=main_menu())


# ──────────────────────────────────────────────────────────────────────────────
#  ПАКЕТНОЕ РАСПИСАНИЕ
# ──────────────────────────────────────────────────────────────────────────────

@router.callback_query(PostFlow.batch_schedule, F.data.startswith("time:category:"))
async def batch_time_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":")[-1]
    await state.update_data(time_category=category)
    await callback.message.edit_reply_markup(reply_markup=time_options_keyboard(category))
    await callback.answer()


@router.callback_query(PostFlow.batch_schedule, F.data == "time:back")
async def batch_time_back(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=batch_schedule_keyboard())
    await callback.answer()


@router.callback_query(PostFlow.batch_schedule, F.data.startswith("schedule:"))
async def batch_schedule_value(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    if parts[1] == "cancel":
        await state.clear()
        await callback.message.answer("❌ Отменено.", reply_markup=main_menu())
        await callback.answer()
        return

    time_period = parts[1]
    time_value = int(parts[2])
    await state.update_data(time_period=time_period, time_value=time_value)

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "📊 Сколько файлов в одном посте?",
        reply_markup=files_per_post_keyboard(),
    )
    await callback.answer()


@router.callback_query(PostFlow.batch_schedule, F.data.startswith("files_per_post:"))
async def batch_files_per_post(callback: CallbackQuery, state: FSMContext):
    files_per_post = int(callback.data.split(":")[-1])
    data = await state.get_data()

    bs = get_batch_service(callback.bot)
    media_files = data.get("media_files", [])
    posts = bs.split_into_posts(media_files, files_per_post)
    schedule = bs.generate_schedule(
        len(posts), data["time_period"], data["time_value"]
    )

    summary = bs.get_batch_summary(len(posts), schedule, data["time_period"], data["time_value"])
    await state.update_data(
        files_per_post=files_per_post,
        posts_count=len(posts),
        schedule=[s.isoformat() for s in schedule],
    )

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        summary,
        reply_markup=confirm_batch_final_keyboard(len(posts)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(PostFlow.batch_schedule, F.data == "batch:confirm_publish")
async def batch_confirm_publish(callback: CallbackQuery, state: FSMContext):
    from datetime import datetime

    data = await state.get_data()
    bs = get_batch_service(callback.bot)
    mp = get_media_processor(callback.bot)

    media_files = data.get("media_files", [])
    files_per_post = data.get("files_per_post", 1)

    # Скачиваем и обрабатываем все файлы
    await callback.message.answer("⏳ Обрабатываю файлы...")
    processed = await mp.process_batch(media_files)

    if not processed:
        await callback.message.answer("❌ Не удалось обработать медиафайлы.")
        await callback.answer()
        return

    posts = bs.split_into_posts(processed, files_per_post)
    schedule = [datetime.fromisoformat(s) for s in data["schedule"]]

    config = load_config()
    cat = data["category_data"]
    platform_id = data["platform_id"]
    thread_id = None
    if data.get("topic"):
        thread_id = (
            config["platforms"][platform_id]["topics"]
            .get(data["topic"], {})
            .get("thread_id")
        )

    count = await bs.schedule_batch(
        posts=posts,
        category_data=cat,
        platform_id=platform_id,
        hashtags=data.get("selected_hashtags", []),
        schedule=schedule,
        thread_id=thread_id,
    )

    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        f"✅ Пакет запланирован: {count} постов",
        reply_markup=after_post_keyboard(),
    )
    await callback.answer()


# ──────────────────────────────────────────────────────────────────────────────
#  ЧЕРНОВИКИ
# ──────────────────────────────────────────────────────────────────────────────

@router.message(F.text == "💾 Черновики")
async def drafts_menu(message: Message, state: FSMContext):
    if not _is_authorized(message.from_user.id):
        return
    await state.clear()
    dm = get_draft_manager()
    drafts = dm.get_user_drafts(message.from_user.id)
    if not drafts:
        await message.answer("📭 У вас нет черновиков.", reply_markup=main_menu())
        return
    await message.answer(
        f"💾 <b>Черновики</b> ({len(drafts)}):",
        reply_markup=drafts_list_keyboard(drafts),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("dr:"))
async def draft_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    action = parts[1]
    draft_id = parts[2] if len(parts) > 2 else None

    dm = get_draft_manager()

    if action == "back":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("🏠 Главное меню", reply_markup=main_menu())
        await callback.answer()
        return

    if action == "list":
        drafts = dm.get_user_drafts(callback.from_user.id)
        await callback.message.edit_reply_markup(
            reply_markup=drafts_list_keyboard(drafts) if drafts else None
        )
        await callback.answer()
        return

    if action == "open":
        draft = dm.get_draft(draft_id)
        if not draft:
            await callback.answer("❌ Черновик не найден")
            return
        info = dm.get_draft_info(draft_id)
        await callback.message.answer(
            info, reply_markup=draft_action_keyboard(draft_id), parse_mode="HTML"
        )
        await callback.answer()
        return

    if action == "del":
        dm.delete_draft(draft_id)
        await callback.answer("🗑️ Черновик удалён")
        drafts = dm.get_user_drafts(callback.from_user.id)
        if drafts:
            await callback.message.edit_reply_markup(
                reply_markup=drafts_list_keyboard(drafts)
            )
        else:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("📭 Черновиков больше нет.", reply_markup=main_menu())
        return

    if action == "pub":
        draft = dm.get_draft(draft_id)
        if not draft:
            await callback.answer("❌ Черновик не найден")
            return
        await callback.message.answer("⏳ Публикую черновик...")
        mp = get_media_processor(callback.bot)
        config = load_config()
        platform_id = draft["platform_id"]
        chat_id = config["platforms"][platform_id]["chat_id"]
        thread_id = None
        if draft.get("topic"):
            thread_id = (
                config["platforms"][platform_id]["topics"]
                .get(draft["topic"], {})
                .get("thread_id")
            )

        processed = await mp.process_batch(draft["media_files"])
        if not processed:
            await callback.message.answer("❌ Не удалось обработать медиафайлы.")
            await callback.answer()
            return

        cat = draft["category_data"]
        emoji_row = cat.get("emoji_row", "")
        invite_link = config["platforms"][platform_id].get("invite_link", "")
        selected = draft.get("selected_hashtags", [])
        hashtag_lines = [" ".join(selected[i : i + 4]) for i in range(0, len(selected), 4)]
        caption = (
            f"{cat['emoji']} {cat['name'].upper()} {cat['emoji']}\n\n"
            f"{emoji_row}\n"
            + "\n".join(hashtag_lines)
            + f"\n{emoji_row}\n\n"
            f"↘️⬇️↙️\n"
            f'<a href="{invite_link}">Ссылка для друга</a>'
        )

        if len(processed) == 1:
            mt, fp = processed[0]
            await mp.send_single_media(chat_id, mt, fp, caption, thread_id)
        else:
            await mp.send_media_group(chat_id, processed, caption, thread_id)

        mp.cleanup_specific([fp for _, fp in processed])
        dm.delete_draft(draft_id)
        await callback.message.answer("✅ Черновик опубликован!", reply_markup=after_post_keyboard())
        await callback.answer()
        return

    if action == "toq":
        draft = dm.get_draft(draft_id)
        if not draft:
            await callback.answer("❌ Черновик не найден")
            return
        qm = get_queue_manager()
        qm.add_to_queue(
            user_id=callback.from_user.id,
            name=draft["name"],
            category_key=draft["category_key"],
            category_data=draft["category_data"],
            platform_id=draft["platform_id"],
            media_files=draft["media_files"],
            selected_hashtags=draft.get("selected_hashtags", []),
            description=draft.get("description", ""),
            topic=draft.get("topic"),
            is_comic=draft.get("is_comic", False),
            comic_link=draft.get("comic_link", ""),
            comic_title=draft.get("comic_title", ""),
        )
        dm.delete_draft(draft_id)
        await callback.answer("📥 Добавлен в очередь")
        await callback.message.answer("📥 Черновик добавлен в очередь.", reply_markup=main_menu())
        return

    await callback.answer()


# ──────────────────────────────────────────────────────────────────────────────
#  ОЧЕРЕДЬ
# ──────────────────────────────────────────────────────────────────────────────

@router.message(F.text == "📥 Очередь")
async def queue_menu(message: Message, state: FSMContext):
    if not _is_authorized(message.from_user.id):
        return
    await state.clear()
    qm = get_queue_manager()
    status = qm.get_status()
    await message.answer(
        qm.get_status_text(),
        reply_markup=queue_menu_keyboard(
            status["running"], status["pending"], status["interval"]
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("q:"))
async def queue_callback(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":", 2)[1]
    qm = get_queue_manager()

    if action == "back":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("🏠 Главное меню", reply_markup=main_menu())
        await callback.answer()
        return

    if action == "menu":
        status = qm.get_status()
        await callback.message.edit_text(
            qm.get_status_text(),
            reply_markup=queue_menu_keyboard(
                status["running"], status["pending"], status["interval"]
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "start":
        mp = get_media_processor(callback.bot)
        ok = await qm.start_queue(mp, callback.bot)
        await callback.answer("▶️ Очередь запущена" if ok else "⚠️ Очередь уже запущена")
        status = qm.get_status()
        await callback.message.edit_reply_markup(
            reply_markup=queue_menu_keyboard(
                status["running"], status["pending"], status["interval"]
            )
        )
        return

    if action == "stop":
        await qm.stop_queue()
        await callback.answer("⏹ Очередь остановлена")
        status = qm.get_status()
        await callback.message.edit_reply_markup(
            reply_markup=queue_menu_keyboard(
                status["running"], status["pending"], status["interval"]
            )
        )
        return

    if action == "setint":
        await callback.message.answer("⏱ Введите интервал в минутах (1–1440):")
        await state.set_state(QueueFlow.set_interval)
        await callback.answer()
        return

    if action == "list":
        items = qm.get_queue()
        if not items:
            await callback.answer("📭 Очередь пуста")
            return
        await callback.message.answer(
            f"📋 Очередь ({len(items)} постов):",
            reply_markup=queue_list_keyboard(items),
        )
        await callback.answer()
        return

    if action == "clear":
        qm.clear_queue()
        await callback.answer("🗑️ Очередь очищена")
        status = qm.get_status()
        await callback.message.edit_text(
            qm.get_status_text(),
            reply_markup=queue_menu_keyboard(
                status["running"], status["pending"], status["interval"]
            ),
            parse_mode="HTML",
        )
        return

    if action == "rm":
        index = int(callback.data.split(":")[-1])
        qm.remove_from_queue(index)
        await callback.answer("🗑️ Пост удалён")
        items = qm.get_queue()
        if items:
            await callback.message.edit_reply_markup(reply_markup=queue_list_keyboard(items))
        else:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("📭 Очередь пуста.", reply_markup=main_menu())
        return

    await callback.answer()


@router.message(QueueFlow.set_interval)
async def queue_set_interval(message: Message, state: FSMContext):
    try:
        minutes = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ Введите число от 1 до 1440.")
        return

    qm = get_queue_manager()
    qm.set_interval(minutes)
    await state.clear()
    status = qm.get_status()
    await message.answer(
        f"✅ Интервал установлен: {qm.interval_minutes} мин",
        reply_markup=queue_menu_keyboard(
            status["running"], status["pending"], status["interval"]
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
#  МОИ ХЕШТЕГИ
# ──────────────────────────────────────────────────────────────────────────────

@router.message(F.text == "#️⃣ Мои хештеги")
async def my_hashtags(message: Message, state: FSMContext):
    if not _is_authorized(message.from_user.id):
        return
    await state.clear()
    await message.answer(
        "#️⃣ <b>Мои хештеги</b>",
        reply_markup=my_hashtags_menu_keyboard(message.from_user.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("myhash:"))
async def myhash_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 3)
    action = parts[1]

    if action == "back":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("🏠 Главное меню", reply_markup=main_menu())
        await callback.answer()
        return

    if action == "menu":
        await callback.message.edit_reply_markup(
            reply_markup=my_hashtags_menu_keyboard(callback.from_user.id)
        )
        await callback.answer()
        return

    if action == "templates":
        await callback.message.edit_reply_markup(
            reply_markup=user_templates_list_keyboard(callback.from_user.id)
        )
        await callback.answer()
        return

    if action == "help":
        text = (
            "ℹ️ <b>Как использовать шаблоны хештегов</b>\n\n"
            "1. При создании поста выберите хештеги\n"
            "2. Нажмите «💾 Сохранить как шаблон»\n"
            "3. В следующий раз выберите «📋 Мои шаблоны»\n\n"
            "Шаблоны сохраняются отдельно для каждой категории."
        )
        await callback.message.answer(text, parse_mode="HTML")
        await callback.answer()
        return

    if action == "cat":
        cat_key = parts[2]
        await callback.message.edit_reply_markup(
            reply_markup=category_templates_keyboard(callback.from_user.id, cat_key)
        )
        await callback.answer()
        return

    if action == "view":
        cat_key = parts[2]
        template_name = parts[3]
        config = load_config()
        tags = (
            config.get("hashtag_templates", {})
            .get(str(callback.from_user.id), {})
            .get(cat_key, {})
            .get(template_name, [])
        )
        text = f"📋 <b>{template_name}</b>\n\n" + " ".join(tags)
        await callback.message.answer(
            text,
            reply_markup=template_actions_keyboard(cat_key, template_name),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "delete":
        cat_key = parts[2]
        template_name = parts[3]
        config = load_config()
        user_str = str(callback.from_user.id)
        config.get("hashtag_templates", {}).get(user_str, {}).get(cat_key, {}).pop(
            template_name, None
        )
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        await callback.answer(f"🗑️ Шаблон «{template_name}» удалён")
        await callback.message.edit_reply_markup(
            reply_markup=user_templates_list_keyboard(callback.from_user.id)
        )
        return

    await callback.answer()


# ──────────────────────────────────────────────────────────────────────────────
#  Заглушка для необработанных сообщений
# ──────────────────────────────────────────────────────────────────────────────

@router.message(StateFilter(None))
async def unknown_message(message: Message):
    if not _is_authorized(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этому боту.")
        return
    await message.answer("🏠 Главное меню", reply_markup=main_menu())
