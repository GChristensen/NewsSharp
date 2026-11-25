"""`reader web` (worker/management/commands/web.py): the web process - one uvicorn server."""
import os
import sqlite3

import uvicorn
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Start the web process (API, frontend, event stream, push callback, image proxy)."

    def handle(self, *args, **options):
        os.environ["NEWSSHARP_PROC"] = "web"
        if sqlite3.sqlite_version_info < (3, 43):
            raise CommandError(f"SQLite 3.43 or newer is required, found {sqlite3.sqlite_version}")
        if not os.environ.get("NEWSSHARP_SUPERVISED"):
            call_command("migrate", interactive=False, verbosity=0)  # started on its own
        if not settings.FRONTEND_DIST.exists():
            self.stderr.write("frontend/dist is missing: run `npm install && npm run build` in frontend/")
        uvicorn.run(
            "config.asgi:application",
            host=settings.BIND,
            port=settings.PORT,
            workers=1,
            lifespan="on",
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=5,
            proxy_headers=False,
            server_header=False,
        )
