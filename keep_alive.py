"""
keep_alive.py — сервер для Render (health check + Bot API для сайта)

Эндпоинты:
  GET  /health            → Render health check (уже был)
  GET  /api/bot/config    → участники, категории, хештеги из config.json
  GET  /api/bot/queue     → текущая очередь + активные пакеты
  GET  /api/bot/drafts    → черновики из drafts.json
  GET  /api/bot/stats     → статистика публикаций (bot_stats.json)
  POST /api/bot/stats     → бот вызывает после каждой публикации

Защита: заголовок X-API-Key или ?key=... (переменная окружения BOT_API_KEY)
CORS: разрешён для всех Origins (сайт на другом домене)
"""

import os
import json
import logging
from pathlib import Path
from datetime import datetime
from aiohttp import web

logger = logging.getLogger(__name__)

# ── Настройки ────────────────────────────────────────────────────────────────

BOT_API_KEY = os.environ.get("BOT_API_KEY", "changeme-set-in-render-env")

# Папки для поиска файлов (в порядке приоритета)
_SEARCH_DIRS = [
    Path("/opt/render/project/.render/botdata"),  # queue.json / running_batches.json
    Path("/opt/render/project/src"),               # config.json, drafts.json, bot_stats.json
    Path("."),                                      # локальная разработка
]


# ── Вспомогательные функции ───────────────────────────────────────────────────

def _load_json(filename: str, default=None):
    """Ищет файл в нескольких папках и возвращает его содержимое."""
    for base in _SEARCH_DIRS:
        path = base / filename
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка чтения {path}: {e}")
    return default if default is not None else {}


def _save_json(filename: str, data) -> bool:
    """Сохраняет файл в папку src (или текущую при локальной разработке)."""
    for base in [Path("/opt/render/project/src"), Path(".")]:
        if base.exists():
            try:
                path = base / filename
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                return True
            except Exception as e:
                logger.error(f"Ошибка сохранения {filename}: {e}")
                break
    return False


def _cors():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
    }


def _json_response(data, status=200):
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str),
        content_type="application/json",
        status=status,
        headers=_cors(),
    )


def _check_auth(request: web.Request) -> bool:
    key = request.headers.get("X-API-Key") or request.rel_url.query.get("key", "")
    return key == BOT_API_KEY


# ── Обработчики ───────────────────────────────────────────────────────────────

async def health(request):
    return web.Response(text="ok", headers=_cors())


async def handle_options(request):
    """Preflight CORS-запросы от браузера."""
    return web.Response(status=200, headers=_cors())


async def api_config(request):
    """Возвращает участников, категории, хештеги и шаблоны."""
    if not _check_auth(request):
        return _json_response({"error": "unauthorized"}, 401)

    config = _load_json("config.json", {})
    result = {
        "participants": config.get("participants", {}),
        "platforms": {
            k: {"name": v.get("name"), "type": v.get("type")}
            for k, v in config.get("platforms", {}).items()
        },
        "hashtag_templates": config.get("hashtag_templates", {}),
    }
    return _json_response(result)


async def api_queue(request):
    """Возвращает ожидающие и активные пакеты публикаций."""
    if not _check_auth(request):
        return _json_response({"error": "unauthorized"}, 401)

    queue = _load_json("queue.json", [])
    running = _load_json("running_batches.json", {})

    return _json_response({
        "queue": queue,
        "running": running,
        "timestamp": datetime.now().isoformat(),
    })


async def api_drafts(request):
    """Возвращает все черновики."""
    if not _check_auth(request):
        return _json_response({"error": "unauthorized"}, 401)

    drafts = _load_json("drafts.json", {})
    return _json_response(drafts)


async def api_stats_get(request):
    """Возвращает лог публикаций бота."""
    if not _check_auth(request):
        return _json_response({"error": "unauthorized"}, 401)

    stats = _load_json("bot_stats.json", {"events": []})
    return _json_response(stats)


async def api_stats_post(request):
    """
    Бот вызывает этот эндпоинт после каждой публикации.
    Тело: { participant, category, platform, count }
    """
    if not _check_auth(request):
        return _json_response({"error": "unauthorized"}, 401)

    try:
        body = await request.json()
    except Exception:
        return _json_response({"error": "invalid json"}, 400)

    stats = _load_json("bot_stats.json", {"events": []})
    stats["events"].append({
        "ts": datetime.now().isoformat(),
        "participant": body.get("participant", "unknown"),
        "category": body.get("category", ""),
        "platform": body.get("platform", ""),
        "count": int(body.get("count", 1)),
    })
    # Храним последние 2000 событий
    stats["events"] = stats["events"][-2000:]
    _save_json("bot_stats.json", stats)

    return _json_response({"ok": True})


# ── Запуск сервера ────────────────────────────────────────────────────────────

async def start_keep_alive_server():
    app = web.Application()

    # Health (уже существовал)
    app.router.add_get("/health", health)
    app.router.add_get("/", health)

    # CORS preflight для всех API-маршрутов
    app.router.add_route("OPTIONS", "/{tail:.*}", handle_options)

    # Bot API
    app.router.add_get("/api/bot/config", api_config)
    app.router.add_get("/api/bot/queue", api_queue)
    app.router.add_get("/api/bot/drafts", api_drafts)
    app.router.add_get("/api/bot/stats", api_stats_get)
    app.router.add_post("/api/bot/stats", api_stats_post)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Keep-alive + Bot API сервер запущен на порту {port}")
