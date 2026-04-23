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
            [KeyboardButton(text="#️⃣ Мои хештеги"), KeyboardButton(text="⚙️ Настройки")],
            [KeyboardButton(text="🧹 Очистить чат"), KeyboardButton(text="🏠 Главная")],
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
            active_keys = data.get("active_categories")  # None = все категории
            row = []
            for cat_key, cat in cats.items():
                if active_keys is not None and cat_key not in active_keys:
                    continue
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


# ============ ТИП ПОСТА (обычный / за звёзды) ============

def post_type_keyboard() -> InlineKeyboardMarkup:
    """Выбор типа поста для канала"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Обычный пост", callback_data="posttype:normal")],
            [InlineKeyboardButton(text="⭐ Пост за Звёзды", callback_data="posttype:stars")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="posttype:cancel")],
        ]
    )


def batch_post_type_keyboard() -> InlineKeyboardMarkup:
    """Выбор типа постов для пакетной отправки на канал"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Обычные посты", callback_data="batch_posttype:normal")],
            [InlineKeyboardButton(text="⭐ Посты за Звёзды", callback_data="batch_posttype:stars")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="schedule:cancel")],
        ]
    )


# ============ ХЕШТЕГИ ============

def hashtags_keyboard(
    all_hashtags: List[str],
    selected: List[str],
    user_id: int,
    cat_key: str,
    show_templates: bool = False,
    delete_mode: bool = False,
) -> InlineKeyboardMarkup:
    rows = []

    if delete_mode:
        # Режим удаления — показываем кастомные хештеги с кнопкой 🗑️
        config = load_config()
        default_tags = set()
        for p in config.get("participants", {}).values():
            cats = p.get("categories", {})
            if cat_key in cats:
                # Стандартные хештеги — те что в оригинальном config (для отображения)
                pass
        # Показываем все хештеги с кнопкой удаления
        row = []
        for tag in all_hashtags:
            row.append(InlineKeyboardButton(
                text=f"🗑️ {tag}",
                callback_data=f"tag:del:{tag}"
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data="tag:delete_mode_off"),
        ])
    else:
        row = []
        for tag in all_hashtags:
            check = "✅ " if tag in selected else ""
            row.append(InlineKeyboardButton(text=f"{check}{tag}", callback_data=f"tag:{tag}"))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)

        rows.append([
            InlineKeyboardButton(text="✅ Все", callback_data="tag:select_all"),
            InlineKeyboardButton(text="❌ Сброс", callback_data="tag:clear_all"),
        ])
        rows.append([
            InlineKeyboardButton(text="➕ Свой хештег", callback_data="tag:add_custom"),
            InlineKeyboardButton(text="🗑️ Удалить хештег", callback_data="tag:delete_mode_on"),
        ])
        rows.append([
            InlineKeyboardButton(text="💾 Сохранить шаблон", callback_data="tag:save_template"),
            InlineKeyboardButton(text="📋 Мои шаблоны", callback_data="tag:templates"),
        ])
        rows.append([
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


def schedule_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏰ Обычное расписание", callback_data="sched_type:normal")],
            [InlineKeyboardButton(text="📅 Создать график", callback_data="sched_type:custom")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="schedule:cancel")],
        ]
    )


def batch_schedule_keyboard() -> InlineKeyboardMarkup:
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
            InlineKeyboardButton(text=f"📄 {name}", callback_data=f"dr:open:{draft_id}")
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

def queue_menu_keyboard(running: bool, count: int, interval: int, has_running_batches: bool = False) -> InlineKeyboardMarkup:
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
    ]
    if has_running_batches:
        rows.append([InlineKeyboardButton(text="📊 Прогресс активных", callback_data="q:progress")])
    rows.append([InlineKeyboardButton(text="🗑️ Очистить очередь", callback_data="q:clear")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="q:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def queue_list_keyboard(items: List[Dict], running: bool = False, active_ids: set = None) -> InlineKeyboardMarkup:
    if active_ids is None:
        active_ids = set()
    rows = []
    pending_index = 0
    for item in items:
        name = item.get("name", "Пост")
        item_type = item.get("type", "single")
        posts_count = len(item.get("posts", [])) if item_type == "batch" else 1
        item_id = item.get("id", "")
        is_running = item.get("_is_running", False)

        icon = "📦" if item_type == "batch" else "📄"
        suffix = f" ×{posts_count}" if item_type == "batch" else ""
        published = item.get("published_count", 0) if is_running else 0
        total = posts_count

        if is_running:
            label = f"▶️ {icon} {name}{suffix} [{published}/{total}]"
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"q:batch_progress:{item_id}"
                )
            ])
        else:
            label = f"{icon} {name}{suffix}"
            rows.append([
                InlineKeyboardButton(text=label, callback_data=f"q:detail:{pending_index}"),
                InlineKeyboardButton(text="🗑️", callback_data=f"q:rm:{pending_index}"),
            ])
            pending_index += 1

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="q:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def queue_item_detail_keyboard(index: int, is_active: bool) -> InlineKeyboardMarkup:
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔀 Совместить — чередовать посты", callback_data="batch_conflict:interleave")],
            [InlineKeyboardButton(text="📅 После текущего пакета", callback_data="batch_conflict:after")],
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
    config = load_config()
    user_str = str(user_id)
    user_templates = config.get("hashtag_templates", {}).get(user_str, {})

    rows = []
    for cat_key, templates in user_templates.items():
        if templates:
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
            [InlineKeyboardButton(text="🗑️ Удалить шаблон", callback_data=f"myhash:delete:{cat_key}:{template_name}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"myhash:cat:{cat_key}")],
        ]
    )


# ============ СОЗДАНИЕ ГРАФИКА ПУБЛИКАЦИЙ ============

def date_selection_keyboard(selected_dates: List[str] = None, days_ahead: int = 14) -> InlineKeyboardMarkup:
    from datetime import datetime, timedelta
    import pytz

    if selected_dates is None:
        selected_dates = []

    kyiv_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kyiv_tz)

    rows = []
    row = []

    for i in range(days_ahead):
        date = now + timedelta(days=i)
        date_str = date.strftime("%d.%m")
        day_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][date.weekday()]

        prefix = "✅ " if date_str in selected_dates else ""
        label = f"{prefix}{day_name} {date_str}"

        row.append(InlineKeyboardButton(
            text=label,
            callback_data=f"customdate:toggle:{date_str}"
        ))

        if len(row) == 3:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    # Кнопки расширения диапазона дат
    range_row = []
    if days_ahead == 14:
        range_row.append(InlineKeyboardButton(text="📅 +2 недели (30 дней)", callback_data="customdate:range:30"))
    elif days_ahead == 30:
        range_row.append(InlineKeyboardButton(text="📅 +месяц (60 дней)", callback_data="customdate:range:60"))
    elif days_ahead == 60:
        range_row.append(InlineKeyboardButton(text="📅 +2 месяца (90 дней)", callback_data="customdate:range:90"))
    if range_row:
        rows.append(range_row)

    rows.append([
        InlineKeyboardButton(text="✅ Выбрать все", callback_data="customdate:all"),
        InlineKeyboardButton(text="❌ Сброс", callback_data="customdate:clear"),
    ])
    rows.append([
        InlineKeyboardButton(text="✔️ Готово", callback_data="customdate:done"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="customdate:back"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def posts_per_day_keyboard() -> InlineKeyboardMarkup:
    rows = []
    row = []

    for n in range(1, 13):
        row.append(InlineKeyboardButton(text=str(n), callback_data=f"postsperday:{n}"))
        if len(row) == 4:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="postsperday:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def time_slots_keyboard(posts_per_day: int, selected_times: List[str] = None) -> InlineKeyboardMarkup:
    if selected_times is None:
        selected_times = []

    rows = []

    if posts_per_day == 1:
        presets = [["12:00"], ["15:00"], ["18:00"], ["20:00"]]
    elif posts_per_day == 2:
        presets = [["10:00", "18:00"], ["12:00", "20:00"], ["14:00", "22:00"]]
    elif posts_per_day == 3:
        presets = [["10:00", "15:00", "20:00"], ["12:00", "16:00", "20:00"], ["14:00", "18:00", "22:00"]]
    elif posts_per_day == 4:
        presets = [
            ["09:00", "13:00", "17:00", "21:00"],
            ["10:00", "14:00", "18:00", "22:00"],
            ["11:00", "15:00", "19:00", "23:00"],
        ]
    elif posts_per_day == 5:
        presets = [
            ["09:00", "12:00", "15:00", "18:00", "21:00"],
            ["10:00", "13:00", "16:00", "19:00", "22:00"],
        ]
    else:
        interval_hours = 24 // posts_per_day
        presets = []
        for start in [8, 9, 10]:
            times = []
            for i in range(posts_per_day):
                hour = (start + i * interval_hours) % 24
                times.append(f"{hour:02d}:00")
            presets.append(times)

    for i, preset in enumerate(presets):
        time_str = ", ".join(preset)
        is_selected = set(selected_times) == set(preset)
        prefix = "✅ " if is_selected else ""
        rows.append([InlineKeyboardButton(
            text=f"{prefix}{time_str}",
            callback_data=f"timeslot:preset:{i}"
        )])

    rows.append([InlineKeyboardButton(text="✏️ Ввести своё время", callback_data="timeslot:custom")])

    if selected_times:
        rows.append([InlineKeyboardButton(
            text=f"✔️ Готово ({len(selected_times)} времен)",
            callback_data="timeslot:done"
        )])

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="timeslot:back")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============ НАСТРОЙКИ (расширенные) ============

def settings_main_keyboard(desc_enabled: bool) -> InlineKeyboardMarkup:
    desc_status = "✅ Вкл" if desc_enabled else "❌ Выкл"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"📝 Описание постов: {desc_status}",
            callback_data="settings:toggle_description"
        )],
        [InlineKeyboardButton(text="📁 Редактор категорий", callback_data="settings:categories")],
        [InlineKeyboardButton(text="🗂️ Каталог категорий", callback_data="settings:catalog")],
        [InlineKeyboardButton(text="🎯 Активные категории", callback_data="settings:active_cats")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="settings:back")],
    ])


# ============ АКТИВНЫЕ КАТЕГОРИИ ============

def active_cats_platform_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Выбор платформы для настройки активных категорий."""
    config = load_config()
    rows = []
    for plat_id, plat_data in config.get("platforms", {}).items():
        plat_icon = "📡" if plat_data.get("type") == "channel" else "👥"
        plat_name = plat_data.get("name", plat_id)
        rows.append([InlineKeyboardButton(
            text=f"{plat_icon} {plat_name}",
            callback_data=f"activecats:platform:{plat_id}"
        )])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="activecats:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def active_cats_select_keyboard(user_id: int, platform_id: str) -> InlineKeyboardMarkup:
    """
    Список категорий пользователя на выбранной платформе.
    Нажатие переключает активность категории.
    """
    config = load_config()

    # Получаем текущие активные ключи
    active_keys = None
    for p_key, p_data in config.get("participants", {}).items():
        if p_data.get("user_id") == user_id:
            active_keys = p_data.get("active_categories")  # None = все активны
            break

    rows = []
    found_any = False
    for p_key, p_data in config.get("participants", {}).items():
        if p_data.get("user_id") != user_id:
            continue
        for cat_key, cat in p_data.get("categories", {}).items():
            if cat.get("platform") != platform_id:
                continue
            found_any = True
            # Если active_keys is None — все включены по умолчанию
            is_active = (active_keys is None) or (cat_key in active_keys)
            check = "✅" if is_active else "☑️"
            rows.append([InlineKeyboardButton(
                text=f"{check} {cat['emoji']} {cat['name']}",
                callback_data=f"activecats:toggle:{platform_id}:{cat_key}"
            )])
        break

    if not found_any:
        rows.append([InlineKeyboardButton(text="📭 Нет категорий на этой платформе", callback_data="activecats:noop")])

    rows.append([InlineKeyboardButton(text="✅ Включить все", callback_data=f"activecats:all:{platform_id}")])
    rows.append([
        InlineKeyboardButton(text="💾 Сохранить", callback_data=f"activecats:save:{platform_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="activecats:platforms"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============ РЕДАКТОР КАТЕГОРИЙ ============

def category_editor_list_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Список категорий пользователя с иконкой платформы."""
    config = load_config()
    rows = []
    for p_key, p_data in config.get("participants", {}).items():
        if p_data.get("user_id") == user_id:
            for cat_key, cat in p_data.get("categories", {}).items():
                platform_id = cat.get("platform", "")
                platform_cfg = config.get("platforms", {}).get(platform_id, {})
                plat_type = platform_cfg.get("type", "group")
                plat_icon = "📡" if plat_type == "channel" else "👥"
                topic_key = cat.get("topic")
                topic_name = ""
                if topic_key:
                    topic_name = platform_cfg.get("topics", {}).get(topic_key, {}).get("name", "")
                topic_str = f" › {topic_name}" if topic_name else ""
                rows.append([InlineKeyboardButton(
                    text=f"{cat['emoji']} {cat['name']} {plat_icon}{topic_str}",
                    callback_data=f"catedit:open:{cat_key}"
                )])
            break
    rows.append([InlineKeyboardButton(text="➕ Добавить категорию", callback_data="catedit:add")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="catedit:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_edit_menu_keyboard(cat_key: str, platform_id: str) -> InlineKeyboardMarkup:
    """Меню редактирования категории с явным переключателем платформы."""
    config = load_config()
    platform_cfg = config.get("platforms", {}).get(platform_id, {})
    plat_name = platform_cfg.get("name", platform_id)
    plat_type = platform_cfg.get("type", "group")
    plat_icon = "📡" if plat_type == "channel" else "👥"

    # Показываем альтернативные платформы как кнопки переключения
    switch_rows = []
    for other_id, other_cfg in config.get("platforms", {}).items():
        if other_id == platform_id:
            continue
        other_icon = "📡" if other_cfg.get("type") == "channel" else "👥"
        other_name = other_cfg.get("name", other_id)
        switch_rows.append(InlineKeyboardButton(
            text=f"↔️ Переключить на {other_icon} {other_name}",
            callback_data=f"catedit:set_platform:{cat_key}:{other_id}"
        ))

    rows = [
        [InlineKeyboardButton(text="✏️ Название", callback_data=f"catedit:edit_name:{cat_key}"),
         InlineKeyboardButton(text="😀 Эмодзи", callback_data=f"catedit:edit_emoji:{cat_key}")],
        [InlineKeyboardButton(text="✨ Строка эмодзи", callback_data=f"catedit:edit_emoji_row:{cat_key}")],
        [InlineKeyboardButton(
            text=f"{plat_icon} Сейчас: {plat_name} — сменить топик",
            callback_data=f"catedit:edit_topic:{cat_key}"
        )],
    ]
    # Кнопки переключения на другие платформы (по одной на строку)
    for btn in switch_rows:
        rows.append([btn])
    rows.append([InlineKeyboardButton(text="🗑️ Удалить категорию", callback_data=f"catedit:delete:{cat_key}")])
    rows.append([InlineKeyboardButton(text="🔙 К списку", callback_data="catedit:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def platform_select_keyboard(cat_key: str = None) -> InlineKeyboardMarkup:
    """Выбор платформы (для создания новой категории)."""
    config = load_config()
    rows = []
    for plat_id, plat_data in config.get("platforms", {}).items():
        plat_icon = "📡" if plat_data.get("type") == "channel" else "👥"
        cb = f"catedit:set_platform:{cat_key}:{plat_id}" if cat_key else f"catedit:newplat:{plat_id}"
        rows.append([InlineKeyboardButton(
            text=f"{plat_icon} {plat_data.get('name', plat_id)}",
            callback_data=cb
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="catedit:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def topic_select_keyboard(platform_id: str, cat_key: str = None) -> InlineKeyboardMarkup:
    """Выбор топика для группы."""
    config = load_config()
    platform_cfg = config.get("platforms", {}).get(platform_id, {})
    topics = platform_cfg.get("topics", {})
    rows = []
    for topic_key, topic_data in topics.items():
        cb = (f"catedit:set_topic:{cat_key}:{topic_key}"
              if cat_key else f"catedit:newtopic:{topic_key}")
        rows.append([InlineKeyboardButton(
            text=f"📌 {topic_data.get('name', topic_key)}",
            callback_data=cb
        )])
    rows.append([InlineKeyboardButton(
        text="— Без топика (общий чат)",
        callback_data=f"catedit:set_topic:{cat_key}:__none__" if cat_key else "catedit:newtopic:__none__"
    )])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"catedit:open:{cat_key}" if cat_key else "catedit:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_category_type_keyboard() -> InlineKeyboardMarkup:
    """Первый шаг добавления категории: выбор откуда взять (шаблон из конфига или создать с нуля)."""
    config = load_config()
    rows = []
    # Собираем уникальные топики из всех платформ как "быстрые шаблоны"
    for plat_id, plat_data in config.get("platforms", {}).items():
        plat_icon = "📡" if plat_data.get("type") == "channel" else "👥"
        plat_name = plat_data.get("name", plat_id)
        topics = plat_data.get("topics", {})
        if topics:
            for topic_key, topic_data in topics.items():
                rows.append([InlineKeyboardButton(
                    text=f"{plat_icon} {plat_name} › 📌 {topic_data.get('name', topic_key)}",
                    callback_data=f"catedit:quickadd:{plat_id}:{topic_key}"
                )])
        else:
            rows.append([InlineKeyboardButton(
                text=f"{plat_icon} {plat_name}",
                callback_data=f"catedit:quickadd:{plat_id}:__none__"
            )])
    rows.append([InlineKeyboardButton(text="✏️ Создать вручную", callback_data="catedit:add_manual")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="catedit:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
