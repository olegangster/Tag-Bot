"""
keep_alive.py — health check + Bot API для сайта
"""
import os
import json
import mimetypes
import logging
from pathlib import Path
from datetime import datetime
from aiohttp import web

logger = logging.getLogger(__name__)

BOT_API_KEY = os.environ.get("BOT_API_KEY", "300610")

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
    Берёт пути к превью следующих постов.
    queue_manager заранее копирует файлы в previews/ при добавлении пакета.
    """
    preview_files = batch.get("preview_files", [])
    fpp  = batch.get("files_per_post", 1)
    done = batch.get("published_count", 0)

    # Пропускаем уже опубликованные файлы
    skip = done * fpp
    upcoming = preview_files[skip : skip + fpp * max_posts]

    result = []
    for item in upcoming:
        if item.get("published"):
            continue
        # Сначала пробуем постоянную копию в previews/
        for path_key in ("path", "original"):
            p = Path(str(item.get(path_key, "")))
            if p.exists() and p.is_file():
                result.append({"path": str(p), "type": item.get("type", "photo")})
                break

    return result

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
    stats["events"].append({"ts": datetime.now().isoformat(), "participant": body.get("participant","unknown"), "category": body.get("category",""), "platform": body.get("platform",""), "count": int(body.get("count",1))})
    stats["events"] = stats["events"][-2000:]
    _save_json("bot_stats.json", stats)
    return _json_resp({"ok": True})

async def api_file(request):
    if not _check_auth(request):
        return web.Response(status=401, headers=_cors())
    file_path_str = request.rel_url.query.get("path", "")
    if not file_path_str:
        return web.Response(status=400, text="missing path", headers=_cors())
    file_path = Path(file_path_str)
    allowed_prefixes = [
        "/tmp", "/var/tmp",
        "/opt/render/project",
        "/home",
        str(Path.cwd()),
    ]
    safe = any(str(file_path).startswith(p) for p in allowed_prefixes)
    if not safe or not file_path.exists() or not file_path.is_file():
        return web.Response(status=404, text="not found", headers=_cors())
    if file_path.stat().st_size > 20 * 1024 * 1024:
        return web.Response(status=413, text="too large", headers=_cors())
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    headers = dict(_cors())
    headers["Cache-Control"] = "private, max-age=300"
    return web.FileResponse(path=file_path, headers=headers)

async def start_keep_alive_server():
    app = web.Application()
    app.router.add_get("/",              health)
    app.router.add_get("/health",        health)
    app.router.add_route("OPTIONS", "/{tail:.*}", handle_options)
    app.router.add_get("/api/bot/config", api_config)
    app.router.add_get("/api/bot/queue",  api_queue)
    app.router.add_get("/api/bot/drafts", api_drafts)
    app.router.add_get("/api/bot/stats",  api_stats_get)
    app.router.add_post("/api/bot/stats", api_stats_post)
    app.router.add_get("/api/bot/file",   api_file)
    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Bot API на порту {port}")
