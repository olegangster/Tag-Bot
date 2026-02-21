"""
Скрипт для получения thread_id всех топиков в супергруппе
Запусти: python get_thread_ids.py
"""
import asyncio
import os
from dotenv import load_dotenv
from aiogram import Bot

load_dotenv()
load_dotenv(".env.txt")

async def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        print("❌ BOT_TOKEN не найден")
        return
    
    bot = Bot(token=bot_token)
    
    # ТУТ УКАЖИ ID СВОЕЙ ГРУППЫ (с минусом!)
    GROUP_ID = -1003827311251  # твой main_group
    
    try:
        # Получаем список топиков
        topics = await bot.get_forum_topic_icon_stickers()  # для иконок
        
        # Пробуем получить информацию через getChat
        chat = await bot.get_chat(GROUP_ID)
        print(f"✅ Группа: {chat.title}")
        print(f"   ID: {chat.id}")
        print(f"   Тип: {chat.type}")
        print(f"   Форум: {getattr(chat, 'is_forum', False)}")
        print()
        
        # Telegram Bot API не даёт прямого метода получить все топики
        # Но можем попробовать отправить в каждый thread_id и посмотреть результат
        
        print("🔍 Проверяю топики из config.json:")
        print()
        
        test_threads = {
            "Главная": 1,
            "Альтушки": 10,
            "3D": 8,
            "Игры": 6,
            "Аниме": 12,
            "Комиксы": 14,
            "Порно": 16
        }
        
        for name, thread_id in test_threads.items():
            try:
                # Пробуем отправить тестовое сообщение
                msg = await bot.send_message(
                    chat_id=GROUP_ID,
                    text=f"🔍 Тест: {name}",
                    message_thread_id=thread_id
                )
                print(f"✅ {name:15} → thread_id: {thread_id}")
                # Сразу удаляем
                await bot.delete_message(GROUP_ID, msg.message_id)
            except Exception as e:
                print(f"❌ {name:15} → thread_id: {thread_id} — {e}")
        
        print()
        print("📝 ИНСТРУКЦИЯ:")
        print("1. Если какой-то топик НЕ работает — открой его в Telegram Desktop")
        print("2. В URL будет: ...?thread=ЧИСЛО")
        print("3. Это ЧИСЛО и есть правильный thread_id")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
