"""
welcome_bot.py — Бот-приветствие для TAG
=========================================
Запускается ОТДЕЛЬНО от основного bot.py!

Флоу:
  1. Пользователь переходит по инвайт-ссылке → пишет /start
  2. Бот рассказывает о сообществе и просит подтвердить 18+
  3. Нет → прощание, больше ничего
  4. Да  → кнопки перехода в канал и группу

Настройка .env.txt:
  WELCOME_BOT_TOKEN=токен_этого_бота   ← ОТДЕЛЬНЫЙ бот от основного!
  ADMIN_IDS=123456789,987654321

Запуск:
  python welcome_bot.py
"""

import asyncio
import logging
import os
import json

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from dotenv import load_dotenv

# ─── Логирование ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─── Окружение ───────────────────────────────────────────────────────────────

load_dotenv()
load_dotenv(".env.txt")

WELCOME_BOT_TOKEN = os.getenv("WELCOME_BOT_TOKEN") or os.getenv("BOT_TOKEN")
if not WELCOME_BOT_TOKEN:
    raise RuntimeError("Не задан WELCOME_BOT_TOKEN (или BOT_TOKEN) в .env.txt")

# ─── Ссылки из config.json ───────────────────────────────────────────────────

def _load_links() -> dict:
    defaults = {
        "group_link":   "https://t.me/+GqZPjm0RLR9iOTQy",
        "channel_link": "https://t.me/+GqZPjm0RLR9iOTQy",
        "referral_bot": "@Tag_Referral_bot",
    }
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for p in cfg.get("platforms", {}).values():
            link = p.get("invite_link", "")
            ref  = p.get("referral_bot", "")
            if p.get("type") == "group" and link:
                defaults["group_link"] = link
            if p.get("type") == "channel" and link:
                defaults["channel_link"] = link
            if ref:
                defaults["referral_bot"] = ref
    except Exception as e:
        logger.warning(f"config.json не прочитан: {e}")
    return defaults

LINKS = _load_links()

# ─── FSM ─────────────────────────────────────────────────────────────────────

class WelcomeFlow(StatesGroup):
    asking_age = State()
    confirmed  = State()

# ─── Клавиатуры ──────────────────────────────────────────────────────────────

def kb_age() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, мне есть 18 лет", callback_data="age:yes")],
        [InlineKeyboardButton(text="❌ Нет, мне нет 18",    callback_data="age:no")],
    ])

def kb_join() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Войти в группу TAG",  url=LINKS["group_link"])],
        [InlineKeyboardButton(text="📢 Перейти в канал TAG", url=LINKS["channel_link"])],
    ])

# ─── Тексты ──────────────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "👋 <b>Добро пожаловать в TAG!</b>\n\n"
    "Мы — закрытое сообщество для взрослых.\n\n"
    "У нас ты найдёшь:\n"
    "  🖤 Альтушки и тёмная эстетика\n"
    "  🎮 3D, игры и аниме\n"
    "  📚 Комиксы\n"
    "  🔞 Эксклюзивный контент 18+\n\n"
    "🔒 <b>Приватка с эксклюзивом</b> — доступна через реферальную программу.\n"
    f"Приводи друзей через <b>{LINKS['referral_bot']}</b> и получай доступ бесплатно!\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ <b>Контент только для лиц старше 18 лет.</b>\n\n"
    "Подтверди свой возраст:"
)

UNDERAGE_TEXT = (
    "🚫 <b>Доступ закрыт</b>\n\n"
    "Наш контент предназначен исключительно "
    "для лиц старше 18 лет.\n\n"
    "До встречи! 👋"
)

CONFIRMED_TEXT = (
    "✅ <b>Возраст подтверждён — добро пожаловать в TAG!</b>\n\n"
    "Нажми кнопку ниже, чтобы присоединиться 👇"
)

# ─── Хэндлеры ────────────────────────────────────────────────────────────────

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(WelcomeFlow.asking_age)
    await message.answer(WELCOME_TEXT, reply_markup=kb_age(), parse_mode="HTML")
    logger.info(f"Новый пользователь: {message.from_user.id} (@{message.from_user.username})")


@router.callback_query(WelcomeFlow.asking_age, F.data == "age:yes")
async def age_yes(callback: CallbackQuery, state: FSMContext):
    await state.set_state(WelcomeFlow.confirmed)
    await callback.message.edit_text(
        CONFIRMED_TEXT,
        reply_markup=kb_join(),
        parse_mode="HTML",
    )
    await callback.answer("✅ Добро пожаловать!")
    logger.info(f"Пользователь {callback.from_user.id} подтвердил 18+")


@router.callback_query(WelcomeFlow.asking_age, F.data == "age:no")
async def age_no(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(UNDERAGE_TEXT, parse_mode="HTML")
    await callback.answer()
    logger.info(f"Пользователь {callback.from_user.id} отказался (нет 18)")


# Устаревшие кнопки после перезапуска бота
@router.callback_query(F.data.in_({"age:yes", "age:no"}))
async def age_stale(callback: CallbackQuery):
    await callback.answer("Нажми /start чтобы начать заново", show_alert=True)


# Любое сообщение вне состояний
@router.message()
async def fallback(message: Message, state: FSMContext):
    if await state.get_state() is None:
        await message.answer("👋 Нажми /start чтобы начать.")


# ─── Запуск ──────────────────────────────────────────────────────────────────

async def main():
    bot = Bot(token=WELCOME_BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    me = await bot.get_me()
    logger.info(f"Welcome-бот запущен: @{me.username} (id={me.id})")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Остановка")
    finally:
        await bot.session.close()
        logger.info("Welcome-бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
