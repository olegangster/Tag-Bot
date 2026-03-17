"""
keep_alive.py — health check + Bot API для сайта (v4 FULL)
С фильтрами по user_id + удаление хештегов + участники + preview_files
"""

import os
import json
import logging
import aiohttp
import mimetypes
from datetime import datetime
from aiohttp import web
from pathlib import Path

logger = logging.getLogger(__name__)

BOT_API_KEY = os.environ.get("BOT_API_KEY", "300610")
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
SITE_URL    = os.environ.get("SITE_URL", "")

_PARTICIPANT_MAP: dict = {}

def _load_participant_map():
    global _PARTICIPANT_MAP
    try:
        for base in [Path("/opt/render/project/.render/botdata"), Path("/opt/render/project/src"), Path(".")]:
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
    if not SITE_URL:
        return
    participant_name = _PARTICIPANT_MAP.get(str(user_id), "")
    if not participant_name:
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                SITE_URL.rstrip("/") + "/api/tracker/bot_post",
                json={
                    "participant_name": participant_name,
                    "count": count,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                },
                headers={"X-Bot-Key": BOT_API_KEY},
                timeout=5
            )
    except Exception as e:
        logger.debug(f"notify_post error: {e}")

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
        path = base / filename
        try:
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
    media_list = batch.get("media_list", [])
    fpp = batch.get("files_per_post", 1)
    done = batch.get("published_count", 0)
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

# ====================== НОВЫЕ ЭНДПОИНТЫ ======================

async def api_queue(request):
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)
    
    user_id_str = request.rel_url.query.get("user_id")
    queue = _load_json("queue.json", [])
    running = _load_json("running_batches.json", {})

    if user_id_str:
        uid = int(user_id_str)
        queue = [item for item in queue if item.get("user_id") == uid]
        running = {k: v for k, v in running.items() if v.get("user_id") == uid}

    for batch_id, batch in running.items():
        try:
            batch["preview_files"] = _extract_preview_files(batch, max_posts=4)
        except Exception:
            batch["preview_files"] = []

    return _json_resp({"queue": queue, "running": running, "timestamp": datetime.now().isoformat()})


async def api_hashtag_delete(request):
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)
    
    cat_key = request.rel_url.query.get("cat_key")
    tag = request.rel_url.query.get("tag")
    user_id = int(request.rel_url.query.get("user_id", 0))
    
    if not cat_key or not tag or not user_id:
        return _json_resp({"error": "missing params"}, 400)
    
    config = _load_json("config.json", {})
    for p in config.get("participants", {}).values():
        if p.get("user_id") == user_id:
            if cat_key in p.get("categories", {}):
                hashtags = p["categories"][cat_key].get("hashtags", [])
                if tag in hashtags:
                    hashtags.remove(tag)
                    _save_json("config.json", config)
                    logger.info(f"Хештег {tag} удалён из {cat_key}")
                    return _json_resp({"ok": True, "message": f"Хештег {tag} удалён"})
    return _json_resp({"error": "not found"}, 404)


async def api_participants(request):
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)
    
    config = _load_json("config.json", {})
    participants = config.get("participants", {})
    
    roles = {
        "oleg": "Главный по Альтушкам + Порно",
        "ira": "Аниме-мастер (Naruto, MHA, One Piece и др.)",
        "adrian": "Комиксы + Сливы",
        "kirill": "Игры + 3D",
        "yulik": "Порно + Аниме",
        "andrey": "Комиксы + 3D",
        "kirill_sunny": "Комиксы + 3D"
    }
    
    for key, p in participants.items():
        p["role"] = roles.get(key, "Постер")
        p["categories_list"] = [f"{c.get('emoji','')} {c.get('name','')}" for c in p.get("categories", {}).values()]
    
    return _json_resp(participants)


# ====================== СТАРЫЕ ЭНДПОИНТЫ ======================

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
            get_file_url = f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}"
            async with session.get(get_file_url, timeout=10) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    return web.Response(status=404, text="file not found", headers=_cors())
                file_path = data["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            async with session.get(download_url, timeout=30) as file_resp:
                if file_resp.status != 200:
                    return web.Response(status=404, text="download failed", headers=_cors())
                content = await file_resp.read()
                content_type = file_resp.headers.get("Content-Type", "application/octet-stream")
                ext = Path(file_path).suffix.lower()
                mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                            ".gif": "image/gif", ".webp": "image/webp", ".mp4": "video/mp4"}
                if ext in mime_map:
                    content_type = mime_map[ext]
                headers = dict(_cors())
                headers["Content-Type"] = content_type
                headers["Cache-Control"] = "private, max-age=3600"
                headers["Content-Length"] = str(len(content))
                return web.Response(body=content, headers=headers)
    except Exception as e:
        logger.error(f"tg_file error: {e}")
        return web.Response(status=502, text="network error", headers=_cors())

async def api_file(request):
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

# ====================== ЗАПУСК ======================
async def start_keep_alive_server():
    _load_participant_map()
    app = web.Application(client_max_size=50 * 1024 * 1024)
    
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_route("OPTIONS", "/{tail:.*}", handle_options)
    
    app.router.add_get("/api/bot/config", api_config)
    app.router.add_get("/api/bot/queue", api_queue)
    app.router.add_get("/api/bot/hashtag/delete", api_hashtag_delete)
    app.router.add_get("/api/bot/participants", api_participants)
    app.router.add_get("/api/bot/drafts", api_drafts)
    app.router.add_get("/api/bot/stats", api_stats_get)
    app.router.add_post("/api/bot/stats", api_stats_post)
    app.router.add_get("/api/bot/tg_file", api_tg_file)
    app.router.add_get("/api/bot/file", api_file)

    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"✅ Bot API запущен на порту {port}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(start_keep_alive_server())
