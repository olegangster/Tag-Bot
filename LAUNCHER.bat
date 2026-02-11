@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo    TELEGRAM BOT LAUNCHER
echo ========================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ОШИБКА: Python не найден!
    echo Установите Python 3.10+ с https://www.python.org/downloads/
    pause
    exit /b 1
)

echo ✅ Python установлен
echo.

echo Установка зависимостей...
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo ⚠️ Ошибка установки зависимостей
)

echo.
echo ========================================
echo    ЗАПУСК БОТА
echo ========================================
echo.

python bot.py

pause
