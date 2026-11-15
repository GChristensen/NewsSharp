"""`reader worker` (worker/management/commands/worker.py): the worker process - coordinator and pool."""
import asyncio
import os
import sqlite3

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def take_lock(path):
    """R23.7: only one worker may run against a database."""
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class Command(BaseCommand):
    help = "Start the worker process (scheduler, fetching, story pipeline, icons, text extraction)."

    def handle(self, *args, **options):
        os.environ["NEWSSHARP_PROC"] = "worker"
        if sqlite3.sqlite_version_info < (3, 43):
            raise CommandError(f"SQLite 3.43 or newer is required, found {sqlite3.sqlite_version}")
        lock = take_lock(settings.DATA_DIR / "worker.lock")
        if lock is None:
            raise CommandError("another worker is already running against this data directory")
        from worker.coordinator import Coordinator

        try:
            asyncio.run(Coordinator().run())
        except KeyboardInterrupt:
            pass
        finally:
            lock.close()
