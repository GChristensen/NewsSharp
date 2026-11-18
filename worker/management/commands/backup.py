"""`reader backup <file>` (worker/management/commands/backup.py): SQLite online backup."""
import sqlite3

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Write a consistent copy of the database to a file. The application may be running."

    def add_arguments(self, parser):
        parser.add_argument("file")

    def handle(self, *args, **options):
        source = sqlite3.connect(settings.DATABASES["default"]["NAME"])
        target = sqlite3.connect(options["file"])
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        self.stdout.write(f"backup written to {options['file']}")
