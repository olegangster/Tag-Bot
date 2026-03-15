"""
keep_alive.py — health check + Bot API для сайта
"""
import os
import json
import mimetypes
import logging
import aiohttp
from pathlib import Path
from datetime import datetime
from aiohttp import web

logger = logging.getLogger(__name__)

BOT_API_KEY = os.environ.get("BOT_API_KEY", "300610")
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")

# 🔧 URL сайта на Fly.io — чтобы бот мог отправлять посты в трекер
# Укажи свой URL: https://tg-sender-myname.fly.dev  (без слеша в конце)
SITE_URL = os.environ.get("SITE_URL", "")

# Маппинг Telegram user_id → имя участника
# Читается из config.json автоматически при старте
_PARTICIPANT_MAP: dict = {}  # {user_id_str: name}

def _load_participant_map():
    """Загружает маппинг участников из config.json."""
    global _PARTICIPANT_MAP
    try:
        for base in _SEARCH_DIRS:
            cfg_path = base / "config.json"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                for key, p in cfg.get("participants", {}).items():
                    uid = str(p.get("user_id", ""))
                    name = p.get("name", "")
                    if uid and name:
                        _PARTICIPANT_MAP[uid] = name
                logger.info(f"Участники загружены: {_PARTICIPANT_MAP}")
                return
    except Exception as e:
        logger.warning(f"Не удалось загрузить участников: {e}")

async def notify_site_post_published(user_id: int, count: int = 1):
    """
    Вызывается после публикации поста — уведомляет сайт (backend.py на Fly)
    чтобы тот обновил счётчик постов в трекере.
    """
    if not SITE_URL:
        return  # SITE_URL не настроен — пропускаем тихо

    participant_name = _PARTICIPANT_MAP.get(str(user_id), "")
    if not participant_name:
        logger.debug(f"notify_post: user_id {user_id} не найден в маппинге")
        return

    try:
        async with aiohttp.ClientSession() as session:
            url = SITE_URL.rstrip("/") + "/api/tracker/bot_post"
            await session.post(
                url,
                json={
                    "participant_name": participant_name,
                    "count": count,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                },
                headers={"X-Bot-Key": BOT_API_KEY},
                timeout=aiohttp.ClientTimeout(total=5),
            )
            logger.info(f"[notify_post] {participant_name} +{count}")
    except Exception as e:
        logger.debug(f"notify_post error: {e}")  # не блокируем публикацию

_SEARCH_DIRS = [
    Path("/opt/render/project/.render/botdata"),
    Path("/opt/render/project/src"),
    Path("."),
]

def _load_json(filename, default=None):
    for base in _SEARCH_DIRS:
        path = base / filename
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка чтения {path}: {e}")
    return default if default is not None else {}

def _save_json(filename, data):
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

def _json_resp(data, status=200):
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str),
        content_type="application/json",
        status=status,
        headers=_cors(),
    )

def _check_auth(request):
    key = request.headers.get("X-API-Key") or request.rel_url.query.get("key", "")
    return key == BOT_API_KEY

def _extract_preview_files(batch, max_posts=4):
    """
    Извлекает file_id файлов следующих постов из media_list.
    media_list содержит Telegram file_id — они идут в /api/bot/tg_file прокси.
    """
    media_list = batch.get("media_list", [])
    fpp  = batch.get("files_per_post", 1)
    done = batch.get("published_count", 0)

    # Берём файлы следующих max_posts постов
    skip = done * fpp
    upcoming = media_list[skip : skip + fpp * max_posts]

    result = []
    for item in upcoming:
        if isinstance(item, dict):
            path = item.get("path", "")
            mtype = item.get("type", "photo")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            mtype, path = item[0], item[1]
        else:
            continue
        if path:
            result.append({"path": str(path), "type": mtype})

    return result

# ── Обработчики ───────────────────────────────────────────────────────────────

async def health(request):
    return web.Response(text="ok", headers=_cors())

async def handle_options(request):
    return web.Response(status=200, headers=_cors())

async def api_config(request):
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)
    config = _load_json("config.json", {})
    return _json_resp({
        "participants": config.get("participants", {}),
        "platforms": {k: {"name": v.get("name"), "type": v.get("type")} for k, v in config.get("platforms", {}).items()},
        "hashtag_templates": config.get("hashtag_templates", {}),
    })

async def api_queue(request):
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)
    queue   = _load_json("queue.json", [])
    running = _load_json("running_batches.json", {})
    for batch_id, batch in running.items():
        try:
            batch["preview_files"] = _extract_preview_files(batch, max_posts=4)
        except Exception:
            batch["preview_files"] = []
    return _json_resp({"queue": queue, "running": running, "timestamp": datetime.now().isoformat()})

async def api_drafts(request):
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)
    return _json_resp(_load_json("drafts.json", {}))

async def api_stats_get(request):
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)
    return _json_resp(_load_json("bot_stats.json", {"events": []}))

async def api_stats_post(request):
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)
    try:
        body = await request.json()
    except Exception:
        return _json_resp({"error": "invalid json"}, 400)
    stats = _load_json("bot_stats.json", {"events": []})
    stats["events"].append({
        "ts": datetime.now().isoformat(),
        "participant": body.get("participant", "unknown"),
        "category": body.get("category", ""),
        "platform": body.get("platform", ""),
        "count": int(body.get("count", 1)),
    })
    stats["events"] = stats["events"][-2000:]
    _save_json("bot_stats.json", stats)
    return _json_resp({"ok": True})

async def api_tg_file(request):
    """
    Прокси для Telegram файлов по file_id.
    GET /api/bot/tg_file?file_id=BAACAgIA...&key=...
    
    Шаги:
    1. Запрашивает у Telegram API ссылку для скачивания
    2. Скачивает файл и отдаёт браузеру напрямую
    """
    if not _check_auth(request):
        return web.Response(status=401, headers=_cors())

    file_id = request.rel_url.query.get("file_id", "")
    if not file_id:
        return web.Response(status=400, text="missing file_id", headers=_cors())

    token = BOT_TOKEN
    if not token:
        return web.Response(status=503, text="BOT_TOKEN not configured", headers=_cors())

    try:
        async with aiohttp.ClientSession() as session:
            # Шаг 1: получаем file_path от Telegram
            get_file_url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
            async with session.get(get_file_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(f"Telegram getFile error for {file_id[:20]}: {data}")
                    return web.Response(status=404, text="file not found in Telegram", headers=_cors())
                file_path = data["result"]["file_path"]

            # Шаг 2: скачиваем файл
            download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=30)) as file_resp:
                if file_resp.status != 200:
                    return web.Response(status=404, text="download failed", headers=_cors())

                content = await file_resp.read()
                content_type = file_resp.headers.get("Content-Type", "application/octet-stream")

                # Определяем тип по расширению если Content-Type неинформативен
                ext = Path(file_path).suffix.lower()
                mime_map = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png", ".gif": "image/gif",
                    ".webp": "image/webp", ".mp4": "video/mp4",
                    ".mov": "video/quicktime", ".avi": "video/avi",
                }
                if ext in mime_map:
                    content_type = mime_map[ext]

                headers = dict(_cors())
                headers["Content-Type"] = content_type
                headers["Cache-Control"] = "private, max-age=3600"
                headers["Content-Length"] = str(len(content))

                return web.Response(body=content, headers=headers)

    except aiohttp.ClientError as e:
        logger.error(f"Ошибка загрузки файла {file_id[:20]}: {e}")
        return web.Response(status=502, text=f"network error: {e}", headers=_cors())
    except Exception as e:
        logger.error(f"Неожиданная ошибка tg_file: {e}")
        return web.Response(status=500, text="internal error", headers=_cors())

async def api_file(request):
    """Отдаёт локальный файл с диска (fallback для уже скачанных файлов)."""
    if not _check_auth(request):
        return web.Response(status=401, headers=_cors())
    file_path_str = request.rel_url.query.get("path", "")
    if not file_path_str:
        return web.Response(status=400, text="missing path", headers=_cors())
    file_path = Path(file_path_str)
    allowed_prefixes = ["/tmp", "/var/tmp", "/opt/render/project", "/home", str(Path.cwd())]
    safe = any(str(file_path).startswith(p) for p in allowed_prefixes)
    if not safe or not file_path.exists() or not file_path.is_file():
        return web.Response(status=404, text="not found", headers=_cors())
    if file_path.stat().st_size > 20 * 1024 * 1024:
        return web.Response(status=413, text="too large", headers=_cors())
    headers = dict(_cors())
    headers["Cache-Control"] = "private, max-age=300"
    return web.FileResponse(path=file_path, headers=headers)

# ── Запуск ────────────────────────────────────────────────────────────────────

async def start_keep_alive_server():
    _load_participant_map()  # загружаем участников из config.json
    app = web.Application(client_max_size=50 * 1024 * 1024)
    app.router.add_get("/",                  health)
    app.router.add_get("/health",            health)
    app.router.add_route("OPTIONS", "/{tail:.*}", handle_options)
    app.router.add_get("/api/bot/config",    api_config)
    app.router.add_get("/api/bot/queue",     api_queue)
    app.router.add_get("/api/bot/drafts",    api_drafts)
    app.router.add_get("/api/bot/stats",     api_stats_get)
    app.router.add_post("/api/bot/stats",    api_stats_post)
    app.router.add_get("/api/bot/tg_file",   api_tg_file)  # ← главный эндпоинт для фото
    app.router.add_get("/api/bot/file",      api_file)     # ← fallback для локальных файлов
    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Bot API на порту {port}")
