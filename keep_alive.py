import os
import logging
from aiohttp import web

logger = logging.getLogger(__name__)

_runner = None  # держим ссылку, чтобы не выгружалось GC

async def health(request):
    return web.Response(text="ok")

async def start_keep_alive_server():
    global _runner

    app = web.Application()
    app.router.add_get("/health", health)

    _runner = web.AppRunner(app)
    await _runner.setup()

    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(_runner, "0.0.0.0", port)
    await site.start()

    logger.warning(f"✅ Health server started on 0.0.0.0:{port} (/health)")
