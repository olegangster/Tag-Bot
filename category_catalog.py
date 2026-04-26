"""
category_catalog.py — Каталог категорий из конфига

Позволяет юзерам добавлять/удалять категории из глобального пула.
Глобальный пул = все уникальные категории из всех participants.
"""

import json
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def load_config() -> dict:
    with open("config.json", "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: dict):
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


# ── Хелперы ──────────────────────────────────────────────────────────────────

def get_all_catalog_categories(config: dict) -> Dict[str, dict]:
    """
    Собирает все уникальные категории из всех участников как «глобальный каталог».
    Ключ = '<participant_key>__<cat_key>' для уникальности.
    Дополнительно добавляет служебное поле '_source' с именем участника-источника.
    """
    catalog: Dict[str, dict] = {}
    for p_key, p_data in config.get("participants", {}).items():
        for cat_key, cat_data in p_data.get("categories", {}).items():
            uid = f"{p_key}__{cat_key}"
            entry = dict(cat_data)
            entry["_source_participant"] = p_key
            entry["_source_cat_key"] = cat_key
            catalog[uid] = entry
    return catalog


def get_user_cat_keys(config: dict, user_id: int) -> Dict[str, dict]:
    """Возвращает словарь категорий пользователя {cat_key: cat_data}."""
    for p_key, p_data in config.get("participants", {}).items():
        if p_data.get("user_id") == user_id:
            return dict(p_data.get("categories", {}))
    return {}


def is_duplicate_category(user_cats: Dict[str, dict], candidate: dict) -> bool:
    """
    Проверяет, есть ли у пользователя уже категория с таким же
    platform+topic. Для каналов (без топика) дополнительно сравнивается
    название категории, чтобы разные категории канала не блокировали друг друга.
    """
    c_platform = candidate.get("platform", "")
    c_topic = candidate.get("topic")
    c_name = candidate.get("name", "").strip().lower()
    for cat in user_cats.values():
        if cat.get("platform") != c_platform:
            continue
        if c_topic is not None:
            # Для групп (с топиком) — уникальность по platform+topic
            if cat.get("topic") == c_topic:
                return True
        else:
            # Для каналов (без топика) — уникальность по platform+name
            if cat.get("topic") is None and cat.get("name", "").strip().lower() == c_name:
                return True
    return False


def user_already_has_category(user_cats: Dict[str, dict], candidate: dict) -> Optional[str]:
    """
    Возвращает ключ категории, если у пользователя уже есть категория
    с таким же platform+topic (для групп) или platform+name (для каналов).
    """
    c_platform = candidate.get("platform", "")
    c_topic = candidate.get("topic")
    c_name = candidate.get("name", "").strip().lower()
    for k, cat in user_cats.items():
        if cat.get("platform") != c_platform:
            continue
        if c_topic is not None:
            if cat.get("topic") == c_topic:
                return k
        else:
            if cat.get("topic") is None and cat.get("name", "").strip().lower() == c_name:
                return k
    return None


def add_category_to_user(user_id: int, catalog_uid: str) -> Tuple[bool, str]:
    """
    Копирует категорию из каталога в профиль пользователя.
    Возвращает (success, message).
    """
    config = load_config()
    catalog = get_all_catalog_categories(config)

    if catalog_uid not in catalog:
        return False, "Категория не найдена в каталоге"

    candidate = catalog[catalog_uid]
    user_cats = get_user_cat_keys(config, user_id)

    existing_key = user_already_has_category(user_cats, candidate)
    if existing_key:
        existing = user_cats[existing_key]
        return False, (
            f"У вас уже есть категория <b>{existing['emoji']} {existing['name']}</b> "
            f"на этой платформе/топике."
        )

    # Генерируем уникальный ключ
    import re as _re
    base_key = _re.sub(r"[^a-zA-Z0-9_]", "", candidate.get("name", "cat").lower().replace(" ", "_"))[:20]
    if not base_key:
        base_key = "cat"

    # Находим участника и добавляем категорию
    for p_key, p_data in config.get("participants", {}).items():
        if p_data.get("user_id") != user_id:
            continue

        cats = p_data.setdefault("categories", {})
        new_key = base_key
        i = 2
        while new_key in cats:
            new_key = f"{base_key}_{i}"
            i += 1

        # Копируем данные категории (без служебных полей _source_*)
        new_cat = {k: v for k, v in candidate.items() if not k.startswith("_source")}
        cats[new_key] = new_cat
        save_config(config)

        platform_cfg = config.get("platforms", {}).get(candidate.get("platform", ""), {})
        plat_name = platform_cfg.get("name", candidate.get("platform", ""))
        topic_key = candidate.get("topic")
        topic_name = ""
        if topic_key:
            topic_name = platform_cfg.get("topics", {}).get(topic_key, {}).get("name", topic_key)

        dest = plat_name + (f" › {topic_name}" if topic_name else "")
        return True, (
            f"✅ Категория <b>{new_cat['emoji']} {new_cat['name']}</b> добавлена!\n"
            f"📍 Публикация: <b>{dest}</b>"
        )

    return False, "Вы не зарегистрированы в системе"


def remove_category_from_user(user_id: int, cat_key: str) -> Tuple[bool, str]:
    """
    Удаляет категорию из профиля пользователя.
    Возвращает (success, message).
    """
    config = load_config()
    for p_key, p_data in config.get("participants", {}).items():
        if p_data.get("user_id") != user_id:
            continue
        cats = p_data.get("categories", {})
        if cat_key not in cats:
            return False, "Категория не найдена"
        cat_name = f"{cats[cat_key].get('emoji', '')} {cats[cat_key].get('name', '')}"
        del cats[cat_key]
        save_config(config)
        return True, f"🗑️ Категория <b>{cat_name}</b> удалена"

    return False, "Вы не зарегистрированы"


# ── Клавиатуры ───────────────────────────────────────────────────────────────

def catalog_keyboard(user_id: int, filter_platform: Optional[str] = None) -> dict:
    """
    Возвращает данные для построения inline-клавиатуры каталога.
    Используется в keyboards.py.
    """
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    config = load_config()
    catalog = get_all_catalog_categories(config)
    user_cats = get_user_cat_keys(config, user_id)

    rows = []

    # Фильтрация по платформе
    items = [
        (uid, cat) for uid, cat in catalog.items()
        if (filter_platform is None or cat.get("platform") == filter_platform)
    ]

    if not items:
        rows.append([InlineKeyboardButton(
            text="📭 Нет доступных категорий", callback_data="catcatalog:noop"
        )])
    else:
        # Группируем по платформе
        by_platform: Dict[str, list] = {}
        for uid, cat in items:
            plat = cat.get("platform", "")
            by_platform.setdefault(plat, []).append((uid, cat))

        for plat_id, plat_items in by_platform.items():
            platform_cfg = config.get("platforms", {}).get(plat_id, {})
            plat_name = platform_cfg.get("name", plat_id)
            plat_type = platform_cfg.get("type", "group")
            plat_icon = "📡" if plat_type == "channel" else "👥"

            # Заголовок платформы
            rows.append([InlineKeyboardButton(
                text=f"── {plat_icon} {plat_name} ──",
                callback_data="catcatalog:noop"
            )])

            for uid, cat in plat_items:
                existing_key = user_already_has_category(user_cats, cat)
                topic_key = cat.get("topic")
                topic_name = ""
                if topic_key:
                    topic_name = platform_cfg.get("topics", {}).get(topic_key, {}).get("name", topic_key)
                topic_str = f" › {topic_name}" if topic_name else ""

                if existing_key:
                    # Уже добавлена — показываем с галочкой и кнопкой удаления
                    rows.append([
                        InlineKeyboardButton(
                            text=f"✅ {cat['emoji']} {cat['name']}{topic_str}",
                            callback_data=f"catcatalog:info:{uid}"
                        ),
                        InlineKeyboardButton(
                            text="🗑️",
                            callback_data=f"catcatalog:remove:{existing_key}"
                        )
                    ])
                else:
                    rows.append([InlineKeyboardButton(
                        text=f"➕ {cat['emoji']} {cat['name']}{topic_str}",
                        callback_data=f"catcatalog:add:{uid}"
                    )])

    # Фильтры по платформе
    filter_rows = []
    all_platforms = config.get("platforms", {})
    if filter_platform is not None:
        filter_rows.append(InlineKeyboardButton(
            text="🔄 Показать все", callback_data="catcatalog:filter:all"
        ))
    for p_id, p_data in all_platforms.items():
        if p_id != filter_platform:
            p_icon = "📡" if p_data.get("type") == "channel" else "👥"
            filter_rows.append(InlineKeyboardButton(
                text=f"{p_icon} {p_data.get('name', p_id)}",
                callback_data=f"catcatalog:filter:{p_id}"
            ))
    if filter_rows:
        rows.append(filter_rows)

    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="settings:categories")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
