"""`reader purge_feeds [--saved] [--folders] [--yes] [--force]`
(worker/management/commands/purge_feeds.py): delete every feed with its stories, read state, icons,
logs, counters and subscriptions, for resetting a test database. Users and preferences are kept."""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from feeds.models import Feed, Setting
from feeds.store import purge_feed
from feeds.util import now
from reading.models import Folder, SavedStory, ViewSetting

# The worker writes its heartbeat every 10 s; a newer one means it is probably running.
RUNNING_WORKER_AGE = 30


class Command(BaseCommand):
    help = "Delete all feeds and their data; optionally saved stories and folders too."

    def add_arguments(self, parser):
        parser.add_argument("--saved", action="store_true", help="also delete all saved stories")
        parser.add_argument("--folders", action="store_true", help="also delete all folders")
        parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
        parser.add_argument("--force", action="store_true", help="run even if the worker seems to be running")

    def handle(self, *args, **options):
        heartbeat = Setting.get("worker_heartbeat")
        if heartbeat and now() - int(heartbeat) < RUNNING_WORKER_AGE and not options["force"]:
            raise CommandError("the worker seems to be running; stop `serve` first or pass --force")

        feed_ids = list(Feed.objects.values_list("id", flat=True))
        targets = [f"{len(feed_ids)} feeds"]
        if options["saved"]:
            targets.append(f"{SavedStory.objects.count()} saved stories")
        if options["folders"]:
            targets.append(f"{Folder.objects.count()} folders")
        if not options["yes"]:
            answer = input(f"Delete {', '.join(targets)}? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                self.stdout.write("Nothing deleted.")
                return

        for feed_id in feed_ids:
            purge_feed(feed_id)
        if options["saved"]:
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute("DELETE FROM saved_fts")
                SavedStory.objects.all().delete()
        if options["folders"]:
            with transaction.atomic():
                ViewSetting.objects.filter(target_type="folder").delete()
                Folder.objects.all().delete()
        self.stdout.write(f"Deleted {', '.join(targets)}.")
