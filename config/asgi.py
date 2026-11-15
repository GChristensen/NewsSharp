"""ASGI entry point of the web process (config/asgi.py). Wraps Django's handler to run the web
process's background loop (worker_events reader, R0.14) inside the server's event loop."""
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application  # noqa: E402

django_app = get_asgi_application()

from api import background  # noqa: E402


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                background.start()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await background.stop()
                await send({"type": "lifespan.shutdown.complete"})
                return
    else:
        await django_app(scope, receive, send)
