import os
from aiohttp import web


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
