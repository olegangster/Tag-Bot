from aiogram.fsm.state import StatesGroup, State


class PostFlow(StatesGroup):
    selecting_category = State()   # выбор категории
    selecting_post_type = State()  # выбор типа поста (обычный / за звёзды) — для канала
    entering_stars_price = State() # ввод цены в звёздах для обычного поста
    entering_batch_stars_price = State()  # ввод цены в звёздах для пакета
    uploading_media    = State()   # загрузка медиа для обычного поста
    selecting_hashtags = State()   # выбор хештегов
    batch_uploading    = State()   # пакетная загрузка медиа
    batch_schedule     = State()   # настройка расписания пакета

    # Комиксы
    comic_link         = State()   # ввод ссылки на комикс
    comic_title        = State()   # ввод названия комикса
    comic_hashtags     = State()   # выбор хештегов для комикса

    # Описание поста (опционально)
    adding_description = State()

    # Создание графика
    custom_schedule_selecting_dates = State()
    custom_schedule_posts_per_day   = State()
    custom_schedule_selecting_times = State()
    custom_schedule_input_time      = State()


class HashtagFlow(StatesGroup):
    adding_custom          = State()
    naming_template        = State()
    selecting_for_template = State()


class QueueFlow(StatesGroup):
    set_interval = State()


class SettingsFlow(StatesGroup):
    pass


class CategoryEditorFlow(StatesGroup):
    # Менеджер категорий
    menu = State()                      # список категорий пользователя
    adding_name = State()               # ввод названия новой категории
    adding_emoji = State()              # ввод эмодзи
    adding_emoji_row = State()          # ввод строки эмодзи
    adding_platform = State()           # выбор платформы
    adding_topic = State()              # выбор топика (для группы)
    # Редактирование существующей
    edit_menu = State()                 # меню редактирования
    edit_name = State()                 # изменение названия
    edit_emoji = State()                # изменение эмодзи
    edit_emoji_row = State()            # изменение строки эмодзи
    edit_platform = State()             # переключение платформы
    edit_topic = State()                # изменение топика
