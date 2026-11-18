"""`reader rebuild_counters [feed id]` (worker/management/commands/rebuild_counters.py)."""
from django.core.management.base import BaseCommand

from feeds.models import Feed
from worker.pipeline import rebuild_counters


class Command(BaseCommand):
    help = "Recompute the story counters of one feed or of all feeds from stored stories."

    def add_arguments(self, parser):
        parser.add_argument("feed_id", nargs="?", type=int)

    def handle(self, *args, **options):
        feeds = Feed.objects.all()
        if options["feed_id"]:
            feeds = feeds.filter(pk=options["feed_id"])
        for feed_id in feeds.values_list("id", flat=True):
            self.stdout.write(f"feed {feed_id}: {rebuild_counters(feed_id)} stories")
