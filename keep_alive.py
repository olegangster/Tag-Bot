import os
import logging
from aiohttp import web

logger = logging.getLogger(__name__)

async def health(request):
    return web.Response(text="ok")

async def start_keep_alive_server():
    app = web.Application()
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.warning(f"✅ Health server started on 0.0.0.0:{port}")
