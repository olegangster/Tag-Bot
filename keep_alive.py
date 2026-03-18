"""
keep_alive.py — health check + Bot API для сайта
"""
import os
import json
import asyncio
import logging
import aiohttp
from pathlib import Path
from datetime import datetime
from aiohttp import web

logger = logging.getLogger(__name__)

BOT_API_KEY = os.environ.get("BOT_API_KEY", "300610")
BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
SITE_URL    = os.environ.get("SITE_URL", "")

_PARTICIPANT_MAP: dict = {}


def _load_participant_map():
    global _PARTICIPANT_MAP
    try:
        for base in _SEARCH_DIRS:
            cfg_path = base / "config.json"
            if cfg_path.exists():
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                for key, p in cfg.get("participants", {}).items():
                    uid  = str(p.get("user_id", ""))
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
        logger.debug(f"notify_post error: {e}")


_SEARCH_DIRS = [
    Path("/data"),
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
    for base in [Path("/data"), Path("/opt/render/project/src"), Path(".")]:
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
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
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
    fpp  = batch.get("files_per_post", 1)
    done = batch.get("published_count", 0)
    skip = done * fpp
    upcoming = media_list[skip: skip + fpp * max_posts]
    result = []
    for item in upcoming:
        if isinstance(item, dict):
            path  = item.get("path", "")
            mtype = item.get("type", "photo")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            mtype, path = item[0], item[1]
        else:
            continue
        if path:
            result.append({"path": str(path), "type": mtype})
    return result


# ── Config write helpers ──────────────────────────────────────────────────────

def _load_config_for_write():
    for base in _SEARCH_DIRS:
        p = base / "config.json"
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f), p
            except Exception as e:
                logger.error(f"Ошибка чтения config.json ({p}): {e}")
    return None, None


def _save_config_to_path(cfg, path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения config.json: {e}")
        return False


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
        "participants":      config.get("participants", {}),
        "platforms":         {k: {"name": v.get("name"), "type": v.get("type")}
                              for k, v in config.get("platforms", {}).items()},
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
        "ts":          datetime.now().isoformat(),
        "participant": body.get("participant", "unknown"),
        "category":    body.get("category", ""),
        "platform":    body.get("platform", ""),
        "count":       int(body.get("count", 1)),
    })
    stats["events"] = stats["events"][-2000:]
    _save_json("bot_stats.json", stats)
    return _json_resp({"ok": True})


async def api_hashtag_delete(request):
    """
    DELETE /api/bot/hashtag?participant=oleg&category=altushki&hashtag=%23alt_girl&key=...
    Удаляет хештег из config.json.
    """
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)

    participant = request.rel_url.query.get("participant", "").strip()
    category    = request.rel_url.query.get("category", "").strip()
    hashtag     = request.rel_url.query.get("hashtag", "").strip()

    if not participant or not category or not hashtag:
        return _json_resp({"error": "participant, category and hashtag are required"}, 400)

    cfg, cfg_path = _load_config_for_write()
    if cfg is None:
        return _json_resp({"error": "config.json not found"}, 500)

    try:
        cat_data = (cfg.get("participants", {})
                       .get(participant, {})
                       .get("categories", {})
                       .get(category))
        if cat_data is None:
            return _json_resp({"error": f"category '{category}' not found"}, 404)

        old_list = cat_data.get("hashtags", [])
        if hashtag not in old_list:
            return _json_resp({"error": "hashtag not found", "hashtag": hashtag}, 404)

        new_list = [h for h in old_list if h != hashtag]
        cfg["participants"][participant]["categories"][category]["hashtags"] = new_list

        if not _save_config_to_path(cfg, cfg_path):
            return _json_resp({"error": "failed to save config"}, 500)

        logger.info(f"[hashtag_delete] {participant}/{category}: удалён '{hashtag}', осталось {len(new_list)}")
        return _json_resp({"ok": True, "participant": participant, "category": category,
                           "removed": hashtag, "remaining": len(new_list)})
    except Exception as e:
        logger.error(f"api_hashtag_delete error: {e}")
        return _json_resp({"error": str(e)}, 500)


async def api_hashtag_add(request):
    """
    POST /api/bot/hashtag
    Body: { participant, category, hashtag }
    Добавляет хештег в config.json.
    """
    if not _check_auth(request):
        return _json_resp({"error": "unauthorized"}, 401)

    try:
        body = await request.json()
    except Exception:
        return _json_resp({"error": "invalid json"}, 400)

    participant = str(body.get("participant", "")).strip()
    category    = str(body.get("category", "")).strip()
    hashtag     = str(body.get("hashtag", "")).strip()

    if not participant or not category or not hashtag:
        return _json_resp({"error": "participant, category and hashtag are required"}, 400)

    if not hashtag.startswith("#"):
        hashtag = "#" + hashtag.replace(" ", "_")

    cfg, cfg_path = _load_config_for_write()
    if cfg is None:
        return _json_resp({"error": "config.json not found"}, 500)

    try:
        cat_data = (cfg.get("participants", {})
                       .get(participant, {})
                       .get("categories", {})
                       .get(category))
        if cat_data is None:
            return _json_resp({"error": f"category '{category}' not found"}, 404)

        existing = cat_data.setdefault("hashtags", [])
        if hashtag in existing:
            return _json_resp({"ok": True, "skipped": True, "msg": "already exists", "total": len(existing)})

        existing.append(hashtag)
        cfg["participants"][participant]["categories"][category]["hashtags"] = existing

        if not _save_config_to_path(cfg, cfg_path):
            return _json_resp({"error": "failed to save config"}, 500)

        logger.info(f"[hashtag_add] {participant}/{category}: добавлен '{hashtag}', итого {len(existing)}")
        return _json_resp({"ok": True, "participant": participant, "category": category,
                           "added": hashtag, "total": len(existing)})
    except Exception as e:
        logger.error(f"api_hashtag_add error: {e}")
        return _json_resp({"error": str(e)}, 500)


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
            async with session.get(get_file_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    return web.Response(status=404, text="file not found in Telegram", headers=_cors())
                file_path = data["result"]["file_path"]

            download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
            async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=30)) as file_resp:
                if file_resp.status != 200:
                    return web.Response(status=404, text="download failed", headers=_cors())

                content      = await file_resp.read()
                content_type = file_resp.headers.get("Content-Type", "application/octet-stream")
                ext = Path(file_path).suffix.lower()
                mime_map = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".png": "image/png",  ".gif":  "image/gif",
                    ".webp": "image/webp", ".mp4": "video/mp4",
                    ".mov": "video/quicktime", ".avi": "video/avi",
                }
                if ext in mime_map:
                    content_type = mime_map[ext]

                headers = dict(_cors())
                headers["Content-Type"]   = content_type
                headers["Cache-Control"]  = "private, max-age=3600"
                headers["Content-Length"] = str(len(content))
                return web.Response(body=content, headers=headers)

    except aiohttp.ClientError as e:
        logger.error(f"Ошибка загрузки файла {file_id[:20]}: {e}")
        return web.Response(status=502, text=f"network error: {e}", headers=_cors())
    except Exception as e:
        logger.error(f"Неожиданная ошибка tg_file: {e}")
        return web.Response(status=500, text="internal error", headers=_cors())


async def api_file(request):
    if not _check_auth(request):
        return web.Response(status=401, headers=_cors())
    file_path_str = request.rel_url.query.get("path", "")
    if not file_path_str:
        return web.Response(status=400, text="missing path", headers=_cors())
    file_path = Path(file_path_str)
    allowed_prefixes = ["/tmp", "/var/tmp", "/data", "/opt/render/project", "/home", str(Path.cwd())]
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
    _load_participant_map()
    app = web.Application(client_max_size=50 * 1024 * 1024)

    # Каждый маршрут — ровно один раз, с защитой от дублей
    _routes = [
        ("GET",    "/",                    health),
        ("GET",    "/health",              health),
        ("OPTIONS","/{tail:.*}",           handle_options),
        ("GET",    "/api/bot/config",      api_config),
        ("GET",    "/api/bot/queue",       api_queue),
        ("GET",    "/api/bot/drafts",      api_drafts),
        ("GET",    "/api/bot/stats",       api_stats_get),
        ("POST",   "/api/bot/stats",       api_stats_post),
        ("GET",    "/api/bot/tg_file",     api_tg_file),
        ("GET",    "/api/bot/file",        api_file),
        ("DELETE", "/api/bot/hashtag",     api_hashtag_delete),
        ("POST",   "/api/bot/hashtag",     api_hashtag_add),
    ]
    for method, path, handler in _routes:
        try:
            if method == "OPTIONS":
                app.router.add_route(method, path, handler)
            elif method == "GET":
                app.router.add_get(path, handler)
            elif method == "POST":
                app.router.add_post(path, handler)
            elif method == "DELETE":
                app.router.add_delete(path, handler)
        except RuntimeError as e:
            logger.warning(f"Маршрут {method} {path} пропущен (дубль): {e}")

    port = int(os.environ.get("PORT", "10000"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Bot API на порту {port}")

    # Держим сервер живым
    while True:
        await asyncio.sleep(3600)
