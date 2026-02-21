from aiogram.fsm.state import StatesGroup, State


class PostFlow(StatesGroup):
    selecting_category = State()   # выбор категории
    uploading_media    = State()   # загрузка медиа для обычного поста
    selecting_hashtags = State()   # выбор хештегов
    batch_uploading    = State()   # пакетная загрузка медиа
    batch_schedule     = State()   # настройка расписания пакета
    
    # Новые состояния для комиксов
    comic_link         = State()   # ввод ссылки на комикс
    comic_title        = State()   # ввод названия комикса
    comic_hashtags     = State()   # выбор хештегов для комикса (опционально)


class HashtagFlow(StatesGroup):
    """Управление пользовательскими хештегами"""
    adding_custom      = State()   # добавление нового хештега
    naming_template    = State()   # название шаблона
    selecting_for_template = State()  # выбор хештегов для шаблона


class QueueFlow(StatesGroup):
    set_interval = State()         # ввод интервала очереди в минутах


# Совместимость со старым импортом
class SettingsFlow(StatesGroup):
    pass
