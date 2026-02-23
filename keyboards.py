"""
keyboards.py — все клавиатуры для бота
"""
import json
from typing import Dict, List, Optional

from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)


def load_config() -> dict:
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


# ============ ГЛАВНОЕ МЕНЮ ============

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Создать пост"), KeyboardButton(text="📦 Пакетная загрузка")],
            [KeyboardButton(text="💾 Черновики"), KeyboardButton(text="📥 Очередь")],
            [KeyboardButton(text="#️⃣ Мои хештеги"), KeyboardButton(text="🧹 Очистить чат")],
            [KeyboardButton(text="🏠 Главная")],
        ],
        resize_keyboard=True,
    )


def after_post_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Ещё пост"), KeyboardButton(text="📦 Пакетная загрузка")],
            [KeyboardButton(text="📥 Очередь"), KeyboardButton(text="💾 Черновики")],
            [KeyboardButton(text="🏠 Главная")],
        ],
        resize_keyboard=True,
    )


# ============ КАТЕГОРИИ ============

def categories_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    config = load_config()
    buttons = []
    for participant_key, data in config.get("participants", {}).items():
        if data.get("user_id") == user_id:
            cats = data.get("categories", {})
            row = []
            for cat_key, cat in cats.items():
                label = f"{cat['emoji']} {cat['name']}"
                row.append(KeyboardButton(text=label))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)
            break
    buttons.append([KeyboardButton(text="🏠 Главная")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


# ============ ЗАГРУЗКА МЕДИА ============

def media_upload_keyboard(count: int) -> ReplyKeyboardMarkup:
    count_label = f"➡️ Далее ({count})" if count else "➡️ Далее"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=count_label)],
            [KeyboardButton(text="💾 В черновик"), KeyboardButton(text="📥 В очередь")],
            [KeyboardButton(text="🗑️ Очистить всё"), KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
    )


# ============ ХЕШТЕГИ ============

def hashtags_keyboard(
    all_hashtags: List[str],
    selected: List[str],
    user_id: int,
    cat_key: str,
    show_templates: bool = False,
) -> InlineKeyboardMarkup:
    rows = []

    # Хештеги по 3 в ряд
    row = []
    for tag in all_hashtags:
        check = "✅ " if tag in selected else ""
        row.append(InlineKeyboardButton(text=f"{check}{tag}", callback_data=f"tag:{tag}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Управляющие кнопки
    rows.append([
        InlineKeyboardButton(text="✅ Все", callback_data="tag:select_all"),
        InlineKeyboardButton(text="❌ Сброс", callback_data="tag:clear_all"),
    ])
    rows.append([
        InlineKeyboardButton(text="➕ Свой хештег", callback_data="tag:add_custom"),
        InlineKeyboardButton(text="💾 Сохранить шаблон", callback_data="tag:save_template"),
    ])
    rows.append([
        InlineKeyboardButton(text="📋 Мои шаблоны", callback_data="tag:templates"),
        InlineKeyboardButton(text="✔️ Готово", callback_data="tag:done"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def hashtag_templates_keyboard(
    user_id: int,
    cat_key: str,
    selected: List[str],
) -> InlineKeyboardMarkup:
    config = load_config()
    user_str = str(user_id)
    templates = (
        config.get("hashtag_templates", {})
        .get(user_str, {})
        .get(cat_key, {})
    )

    rows = []
    for tmpl_name, tmpl_tags in templates.items():
        is_active = all(t in selected for t in tmpl_tags)
        prefix = "✅ " if is_active else ""
        rows.append([
            InlineKeyboardButton(
                text=f"{prefix}{tmpl_name} ({len(tmpl_tags)})",
                callback_data=f"tmpl:apply:{tmpl_name}"
            )
        ])

    if not rows:
        rows.append([InlineKeyboardButton(text="📭 Шаблонов нет", callback_data="tmpl:noop")])

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="tag:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============ КОМИКСЫ ============

def comic_input_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
    )


def comic_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Опубликовать", callback_data="comic:publish")],
            [
                InlineKeyboardButton(text="✏️ Изменить название", callback_data="comic:edit_title"),
                InlineKeyboardButton(text="🔗 Изменить ссылку", callback_data="comic:edit_link"),
            ],
            [InlineKeyboardButton(text="#️⃣ Добавить хештеги", callback_data="comic:add_hashtags")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="comic:cancel")],
        ]
    )


# ============ ПАКЕТНАЯ ЗАГРУЗКА ============

def files_per_post_keyboard() -> InlineKeyboardMarkup:
    """Сколько файлов в одном посте"""
    rows = []
    row = []
    for n in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]:
        row.append(InlineKeyboardButton(text=str(n), callback_data=f"files_per_post:{n}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="schedule:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def batch_schedule_keyboard() -> InlineKeyboardMarkup:
    """Выбор единицы времени для расписания"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏱ Минуты", callback_data="time:category:minute"),
                InlineKeyboardButton(text="🕐 Часы", callback_data="time:category:hour"),
            ],
            [
                InlineKeyboardButton(text="📅 Дни", callback_data="time:category:day"),
                InlineKeyboardButton(text="📆 Недели", callback_data="time:category:week"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="schedule:cancel")],
        ]
    )


def time_options_keyboard(time_cat: str) -> InlineKeyboardMarkup:
    """Выбор конкретного значения времени"""
    options = {
        "minute": [5, 10, 15, 20, 30, 45, 60, 90, 120],
        "hour":   [1, 2, 3, 4, 6, 8, 12, 18, 24],
        "day":    [1, 2, 3, 4, 5, 6, 7, 10, 14],
        "week":   [1, 2, 3, 4],
    }
    labels = {
        "minute": "мин",
        "hour":   "ч",
        "day":    "д",
        "week":   "нед",
    }
    values = options.get(time_cat, [1, 2, 3])
    label = labels.get(time_cat, "")

    rows = []
    row = []
    for v in values:
        row.append(
            InlineKeyboardButton(
                text=f"{v} {label}",
                callback_data=f"schedule:{time_cat}:{v}"
            )
        )
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="time:back")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="schedule:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def batch_shuffle_keyboard() -> InlineKeyboardMarkup:
    """Перемешать посты или нет"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔀 Да, перемешать", callback_data="shuffle:yes"),
                InlineKeyboardButton(text="➡️ Нет, по порядку", callback_data="shuffle:no"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="shuffle:cancel")],
        ]
    )


def confirm_batch_final_keyboard(posts_count: int) -> InlineKeyboardMarkup:
    """Финальное подтверждение публикации пакета"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"📤 Опубликовать {posts_count} постов",
                    callback_data="batch:confirm_publish"
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="shuffle:cancel")],
        ]
    )


# ============ ЧЕРНОВИКИ ============

def drafts_list_keyboard(user_drafts: Dict) -> InlineKeyboardMarkup:
    rows = []
    for draft_id, draft in user_drafts.items():
        name = draft.get("name", draft_id)
        rows.append([
            InlineKeyboardButton(
                text=f"📄 {name}",
                callback_data=f"dr:open:{draft_id}"
            )
        ])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="dr:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def draft_action_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"dr:pub:{draft_id}")],
            [InlineKeyboardButton(text="📥 В очередь", callback_data=f"dr:toq:{draft_id}")],
            [InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"dr:del:{draft_id}")],
            [InlineKeyboardButton(text="🔙 К списку", callback_data="dr:list")],
        ]
    )


# ============ ОЧЕРЕДЬ ============

def queue_menu_keyboard(running: bool, count: int, interval: int) -> InlineKeyboardMarkup:
    start_stop = (
        InlineKeyboardButton(text="⏹ Остановить", callback_data="q:stop")
        if running
        else InlineKeyboardButton(text="▶️ Запустить", callback_data="q:start")
    )
    rows = [
        [start_stop],
        [
            InlineKeyboardButton(text=f"📋 Список ({count})", callback_data="q:list"),
            InlineKeyboardButton(text=f"⏱ Интервал: {interval}м", callback_data="q:setint"),
        ],
        [InlineKeyboardButton(text="🗑️ Очистить очередь", callback_data="q:clear")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="q:back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def queue_list_keyboard(items: List[Dict], running: bool = False) -> InlineKeyboardMarkup:
    rows = []
    for i, item in enumerate(items):
        name = item.get("name", f"Пост #{i+1}")
        item_type = item.get("type", "single")
        posts_count = len(item.get("posts", [])) if item_type == "batch" else 1

        if item_type == "batch":
            icon = "📦"
            suffix = f" ×{posts_count}"
        else:
            icon = "📄"
            suffix = ""

        label = f"{icon} {name}{suffix}"

        # Активно публикуемый — нельзя удалить
        if running and i == 0:
            rows.append([
                InlineKeyboardButton(
                    text=f"▶️ {label} [публикуется]",
                    callback_data=f"q:detail:{i}"
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(text=label, callback_data=f"q:detail:{i}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"q:rm:{i}"),
            ])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="q:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def queue_item_detail_keyboard(index: int, is_active: bool) -> InlineKeyboardMarkup:
    """Клавиатура для детального просмотра элемента очереди"""
    rows = []
    if is_active:
        rows.append([InlineKeyboardButton(
            text="▶️ Сейчас публикуется — нельзя удалить",
            callback_data="q:noop"
        )])
    else:
        rows.append([InlineKeyboardButton(
            text="🗑️ Удалить этот элемент",
            callback_data=f"q:rm:{index}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 К списку очереди", callback_data="q:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def batch_conflict_keyboard() -> InlineKeyboardMarkup:
    """Выбор режима при конфликте пакетов"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔀 Совместить — чередовать посты",
                    callback_data="batch_conflict:interleave"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 После текущего пакета",
                    callback_data="batch_conflict:after"
                )
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="batch_conflict:cancel")],
        ]
    )


# ============ МОИ ХЕШТЕГИ ============

def my_hashtags_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои шаблоны", callback_data="myhash:templates")],
            [InlineKeyboardButton(text="ℹ️ Справка", callback_data="myhash:help")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="myhash:back")],
        ]
    )


def user_templates_list_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Список категорий с шаблонами"""
    config = load_config()
    user_str = str(user_id)
    user_templates = config.get("hashtag_templates", {}).get(user_str, {})

    rows = []
    for cat_key, templates in user_templates.items():
        if templates:
            # Находим название категории
            cat_name = cat_key
            for p_key, p_data in config.get("participants", {}).items():
                if p_data.get("user_id") == user_id:
                    cat_info = p_data.get("categories", {}).get(cat_key, {})
                    if cat_info:
                        cat_name = f"{cat_info.get('emoji','')} {cat_info.get('name', cat_key)}"
                    break
            rows.append([
                InlineKeyboardButton(
                    text=f"{cat_name} ({len(templates)})",
                    callback_data=f"myhash:cat:{cat_key}"
                )
            ])

    if not rows:
        rows.append([InlineKeyboardButton(text="📭 Шаблонов нет", callback_data="myhash:noop")])

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="myhash:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_templates_keyboard(user_id: int, cat_key: str) -> InlineKeyboardMarkup:
    """Список шаблонов категории"""
    config = load_config()
    user_str = str(user_id)
    templates = (
        config.get("hashtag_templates", {})
        .get(user_str, {})
        .get(cat_key, {})
    )

    rows = []
    for tmpl_name, tmpl_tags in templates.items():
        rows.append([
            InlineKeyboardButton(
                text=f"📋 {tmpl_name} ({len(tmpl_tags)})",
                callback_data=f"myhash:view:{cat_key}:{tmpl_name}"
            )
        ])

    if not rows:
        rows.append([InlineKeyboardButton(text="📭 Шаблонов нет", callback_data="myhash:noop")])

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="myhash:templates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def template_actions_keyboard(cat_key: str, template_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑️ Удалить шаблон",
                    callback_data=f"myhash:delete:{cat_key}:{template_name}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад",
                    callback_data=f"myhash:cat:{cat_key}"
                )
            ],
        ]
    )
