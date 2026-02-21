from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from typing import List, Dict
import json


def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)


# ──────────────────────────────────────────────────────
#  ГЛАВНОЕ МЕНЮ
# ──────────────────────────────────────────────────────
def main_menu() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="➕ Создать пост"))
    kb.row(KeyboardButton(text="📦 Пакетная загрузка"))
    kb.row(
        KeyboardButton(text="💾 Черновики"),
        KeyboardButton(text="📥 Очередь"),
    )
    kb.row(
        KeyboardButton(text="#️⃣ Мои хештеги"),
        KeyboardButton(text="🧹 Очистить чат"),
    )
    return kb.as_markup(resize_keyboard=True)


# ──────────────────────────────────────────────────────
#  ВЫБОР КАТЕГОРИИ
# ──────────────────────────────────────────────────────
def categories_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    config = load_config()
    kb = ReplyKeyboardBuilder()
    for _, data in config['participants'].items():
        if data.get('user_id') == user_id:
            for cat_data in data['categories'].values():
                emoji = cat_data.get('emoji', '📁')
                kb.add(KeyboardButton(text=f"{emoji} {cat_data['name']}"))
            break
    kb.row(KeyboardButton(text="🏠 Главная"))
    kb.adjust(2)
    return kb.as_markup(resize_keyboard=True)


# ──────────────────────────────────────────────────────
#  ЗАГРУЗКА МЕДИА
# ──────────────────────────────────────────────────────
def media_upload_keyboard(count: int = 0) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    label = f"➡️ Далее ({count})" if count > 0 else "➡️ Далее"
    kb.row(KeyboardButton(text=label))
    kb.row(
        KeyboardButton(text="💾 В черновик"),
        KeyboardButton(text="📥 В очередь"),
    )
    kb.row(
        KeyboardButton(text="🗑️ Очистить всё"),
        KeyboardButton(text="❌ Отмена"),
    )
    return kb.as_markup(resize_keyboard=True)


# ──────────────────────────────────────────────────────
#  КОМИКСЫ - ВВОД ДАННЫХ
# ──────────────────────────────────────────────────────
def comic_input_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура для ввода данных комикса"""
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="❌ Отмена"))
    return kb.as_markup(resize_keyboard=True)


def comic_confirm_keyboard() -> InlineKeyboardMarkup:
    """Подтверждение публикации комикса"""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 Опубликовать", callback_data="comic:publish"))
    kb.row(InlineKeyboardButton(text="#️⃣ Добавить хештеги", callback_data="comic:add_hashtags"))
    kb.row(InlineKeyboardButton(text="📝 Изменить название", callback_data="comic:edit_title"))
    kb.row(InlineKeyboardButton(text="🔗 Изменить ссылку", callback_data="comic:edit_link"))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="comic:cancel"))
    return kb.as_markup()


# ──────────────────────────────────────────────────────
#  ХЭШТЕГИ - ОСНОВНАЯ КЛАВИАТУРА
# ──────────────────────────────────────────────────────
def hashtags_keyboard(hashtags: List[str], selected: List[str], 
                      user_id: int = None, category_key: str = None,
                      show_templates: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура выбора хештегов с шаблонами"""
    kb = InlineKeyboardBuilder()
    
    # Хештеги
    for tag in hashtags:
        mark = "✅" if tag in selected else "⬜"
        kb.add(InlineKeyboardButton(
            text=f"{mark} {tag}",
            callback_data=f"tag:{tag}",
        ))
    kb.adjust(2)
    
    # Быстрые действия
    kb.row(
        InlineKeyboardButton(text="✅ Все", callback_data="tag:select_all"),
        InlineKeyboardButton(text="❌ Сбросить", callback_data="tag:clear_all"),
    )
    
    # Шаблоны и пользовательские хештеги
    if show_templates and user_id and category_key:
        config = load_config()
        user_str = str(user_id)
        if user_str in config.get('hashtag_templates', {}):
            if category_key in config['hashtag_templates'][user_str]:
                kb.row(InlineKeyboardButton(
                    text="📋 Мои шаблоны",
                    callback_data="tag:templates"
                ))
    
    kb.row(
        InlineKeyboardButton(text="➕ Свой хештег", callback_data="tag:add_custom"),
        InlineKeyboardButton(text="💾 Сохранить как шаблон", callback_data="tag:save_template"),
    )
    
    kb.row(InlineKeyboardButton(text="🚀 Готово", callback_data="tag:done"))
    
    return kb.as_markup()


def hashtag_templates_keyboard(user_id: int, category_key: str, 
                               selected: List[str]) -> InlineKeyboardMarkup:
    """Клавиатура выбора шаблонов хештегов"""
    config = load_config()
    kb = InlineKeyboardBuilder()
    
    user_str = str(user_id)
    templates = config.get('hashtag_templates', {}).get(user_str, {}).get(category_key, {})
    
    for template_name, template_tags in templates.items():
        # Проверяем, активен ли этот шаблон
        is_active = all(tag in selected for tag in template_tags)
        mark = "✅" if is_active else "📋"
        kb.row(InlineKeyboardButton(
            text=f"{mark} {template_name}",
            callback_data=f"tmpl:apply:{template_name}"
        ))
    
    kb.row(InlineKeyboardButton(text="🔙 Назад к хештегам", callback_data="tag:back"))
    return kb.as_markup()


# ──────────────────────────────────────────────────────
#  УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЬСКИМИ ХЕШТЕГАМИ
# ──────────────────────────────────────────────────────
def my_hashtags_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Главное меню управления хештегами"""
    config = load_config()
    kb = InlineKeyboardBuilder()
    
    user_str = str(user_id)
    templates_count = 0
    if user_str in config.get('hashtag_templates', {}):
        for cat_templates in config['hashtag_templates'][user_str].values():
            templates_count += len(cat_templates)
    
    kb.row(InlineKeyboardButton(
        text=f"📋 Мои шаблоны ({templates_count})",
        callback_data="myhash:templates"
    ))
    kb.row(InlineKeyboardButton(
        text="ℹ️ Как использовать",
        callback_data="myhash:help"
    ))
    kb.row(InlineKeyboardButton(text="🔙 Главная", callback_data="myhash:back"))
    
    return kb.as_markup()


def user_templates_list_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Список всех шаблонов пользователя"""
    config = load_config()
    kb = InlineKeyboardBuilder()
    
    user_str = str(user_id)
    templates = config.get('hashtag_templates', {}).get(user_str, {})
    
    if not templates:
        kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="myhash:menu"))
        return kb.as_markup()
    
    for category_key, cat_templates in templates.items():
        # Получаем имя категории
        cat_name = category_key
        for part_data in config['participants'].values():
            if part_data.get('user_id') == user_id:
                if category_key in part_data.get('categories', {}):
                    cat_name = part_data['categories'][category_key].get('name', category_key)
                    break
        
        kb.row(InlineKeyboardButton(
            text=f"📁 {cat_name}",
            callback_data=f"myhash:cat:{category_key}"
        ))
    
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="myhash:menu"))
    return kb.as_markup()


def category_templates_keyboard(user_id: int, category_key: str) -> InlineKeyboardMarkup:
    """Шаблоны для конкретной категории"""
    config = load_config()
    kb = InlineKeyboardBuilder()
    
    user_str = str(user_id)
    templates = config.get('hashtag_templates', {}).get(user_str, {}).get(category_key, {})
    
    for template_name, tags in templates.items():
        kb.row(InlineKeyboardButton(
            text=f"📋 {template_name} ({len(tags)} тегов)",
            callback_data=f"myhash:view:{category_key}:{template_name}"
        ))
    
    kb.row(InlineKeyboardButton(text="🔙 К категориям", callback_data="myhash:templates"))
    return kb.as_markup()


def template_actions_keyboard(category_key: str, template_name: str) -> InlineKeyboardMarkup:
    """Действия с шаблоном"""
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text="🗑️ Удалить шаблон",
        callback_data=f"myhash:delete:{category_key}:{template_name}"
    ))
    kb.row(InlineKeyboardButton(
        text="🔙 Назад",
        callback_data=f"myhash:cat:{category_key}"
    ))
    return kb.as_markup()


# ──────────────────────────────────────────────────────
#  ПАКЕТ — единица времени
# ──────────────────────────────────────────────────────
def batch_schedule_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="⏱️ Минуты",  callback_data="time:category:minute"))
    kb.row(InlineKeyboardButton(text="🕐 Часы",    callback_data="time:category:hour"))
    kb.row(InlineKeyboardButton(text="📅 Дни",     callback_data="time:category:day"))
    kb.row(InlineKeyboardButton(text="📆 Недели",  callback_data="time:category:week"))
    kb.row(InlineKeyboardButton(text="❌ Отмена",  callback_data="schedule:cancel"))
    return kb.as_markup()


# ──────────────────────────────────────────────────────
#  ПАКЕТ — конкретное значение
# ──────────────────────────────────────────────────────
def time_options_keyboard(category: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    opts = {
        "minute": [("5 мин",  5), ("10 мин", 10), ("15 мин", 15),
                   ("30 мин", 30), ("45 мин", 45), ("60 мин", 60)],
        "hour":   [("1 ч",   1), ("2 ч",   2), ("3 ч",   3),
                   ("6 ч",   6), ("12 ч",  12), ("24 ч",  24)],
        "day":    [("1 день",  1), ("2 дня",  2), ("3 дня",  3),
                   ("5 дней",  5), ("7 дней",  7), ("10 дней", 10)],
        "week":   [("1 нед",  1), ("2 нед",  2), ("3 нед",  3), ("4 нед", 4)],
    }
    for label, val in opts.get(category, []):
        kb.add(InlineKeyboardButton(
            text=label,
            callback_data=f"schedule:{category}:{val}",
        ))
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="⬅️ Назад",  callback_data="time:back"))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="schedule:cancel"))
    return kb.as_markup()


# ──────────────────────────────────────────────────────
#  ПАКЕТ — файлов в посте
# ──────────────────────────────────────────────────────
def files_per_post_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    options = [
        (1,  "1 файл = 1 пост"),
        (2,  "2 файла = 1 пост"),
        (3,  "3 файла = 1 пост"),
        (4,  "4 файла = 1 пост"),
        (5,  "5 файлов = 1 пост"),
        (10, "10 файлов = 1 пост"),
    ]
    for n, label in options:
        kb.add(InlineKeyboardButton(
            text=label,
            callback_data=f"files_per_post:{n}",
        ))
    kb.adjust(2)
    return kb.as_markup()


# ──────────────────────────────────────────────────────
#  ПАКЕТ — подтверждение
# ──────────────────────────────────────────────────────
def confirm_batch_final_keyboard(posts_count: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(
        text=f"🚀 Опубликовать {posts_count} постов",
        callback_data="batch:confirm_publish",
    ))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="schedule:cancel"))
    return kb.as_markup()


# ──────────────────────────────────────────────────────
#  ПОСЛЕ ПУБЛИКАЦИИ
# ──────────────────────────────────────────────────────
def after_post_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="➕ Ещё пост"))
    kb.row(KeyboardButton(text="📦 Пакетная загрузка"))
    kb.row(KeyboardButton(text="🏠 Главная"))
    return kb.as_markup(resize_keyboard=True)


# ──────────────────────────────────────────────────────
#  ЧЕРНОВИКИ
# ──────────────────────────────────────────────────────
def drafts_list_keyboard(drafts: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for did, d in drafts.items():
        name  = d.get('name', did)
        cat   = d.get('category_name', '?')
        
        # Проверяем тип черновика
        if d.get('is_comic'):
            n = "🔗 комикс"
        else:
            n = f"{len(d.get('media_files', []))} ф."
        
        kb.row(InlineKeyboardButton(
            text=f"📄 {name}  •  {cat}  •  {n}",
            callback_data=f"dr:open:{did}",
        ))
    kb.row(InlineKeyboardButton(text="🔙 Главная", callback_data="dr:back"))
    return kb.as_markup()


def draft_action_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="🚀 Опубликовать",          callback_data=f"dr:pub:{draft_id}"))
    kb.row(InlineKeyboardButton(text="📥 Добавить в очередь",    callback_data=f"dr:toq:{draft_id}"))
    kb.row(InlineKeyboardButton(text="🗑️ Удалить",               callback_data=f"dr:del:{draft_id}"))
    kb.row(InlineKeyboardButton(text="⬅️ К списку черновиков",   callback_data="dr:list"))
    return kb.as_markup()


# ──────────────────────────────────────────────────────
#  ОЧЕРЕДЬ
# ──────────────────────────────────────────────────────
def queue_menu_keyboard(running: bool, count: int, interval: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if running:
        kb.row(InlineKeyboardButton(text="⏹ Остановить очередь",     callback_data="q:stop"))
    else:
        if count > 0:
            kb.row(InlineKeyboardButton(
                text=f"▶️ Запустить  ({count} постов)",
                callback_data="q:start",
            ))
    kb.row(InlineKeyboardButton(
        text=f"⏱ Интервал: {interval} мин",
        callback_data="q:setint",
    ))
    kb.row(InlineKeyboardButton(text="📋 Список постов",             callback_data="q:list"))
    if count > 0:
        kb.row(InlineKeyboardButton(text="🗑️ Очистить очередь",     callback_data="q:clear"))
    kb.row(InlineKeyboardButton(text="🔙 Главная",                   callback_data="q:back"))
    return kb.as_markup()


def queue_list_keyboard(items: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, item in enumerate(items[:20]):
        name = item.get('name', f'Пост {i+1}')
        
        # Проверяем тип поста
        if item.get('is_comic'):
            n = "🔗 комикс"
        else:
            n = f"({len(item.get('media_files', []))} ф.)"
        
        kb.row(InlineKeyboardButton(
            text=f"🗑️ #{i+1}  {name}  {n}",
            callback_data=f"q:rm:{i}",
        ))
    kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="q:menu"))
    return kb.as_markup()
