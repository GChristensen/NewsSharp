"""Feed-side data model (feeds/models.py), item 1 of the requirements. All times are UTC integer seconds."""
import secrets

from django.db import models


class Setting(models.Model):
    """Instance settings: public_base_url, secrets, worker heartbeat."""

    key = models.CharField(max_length=64, primary_key=True)
    value = models.TextField(default="")

    @classmethod
    def get(cls, key: str, default: str | None = None) -> str | None:
        row = cls.objects.filter(pk=key).values_list("value", flat=True).first()
        return default if row is None else row

    @classmethod
    def put(cls, key: str, value: str) -> None:
        cls.objects.update_or_create(pk=key, defaults={"value": value})

    @classmethod
    def secret(cls, key: str) -> str:
        """A random secret generated at first use and stored (R14.4)."""
        value = cls.get(key)
        if not value:
            cls.objects.get_or_create(pk=key, defaults={"value": secrets.token_hex(32)})
            value = cls.get(key)
        return value


class Feed(models.Model):
    feed_address = models.CharField(max_length=764, unique=True)
    feed_link = models.CharField(max_length=1000, blank=True, default="")
    feed_link_locked = models.BooleanField(default=False)
    title = models.CharField(max_length=255, blank=True, default="")
    tagline = models.TextField(blank=True, default="")
    image_url = models.CharField(max_length=1000, blank=True, default="")  # <image>/<icon>/<logo>, R8.2
    etag = models.CharField(max_length=255, blank=True, default="")
    last_modified = models.CharField(max_length=64, blank=True, default="")
    fetched_once = models.BooleanField(default=False)
    known_good = models.BooleanField(default=False)
    has_exception = models.BooleanField(default=False)
    exception_code = models.IntegerField(default=0)
    errors_since_good = models.IntegerField(default=0)
    redirects_in_row = models.IntegerField(default=0)  # consecutive permanent redirects, R2.8
    last_update = models.IntegerField(default=0)
    next_scheduled_update = models.IntegerField(default=0, db_index=True)
    min_to_decay = models.IntegerField(default=0)
    last_load_ms = models.IntegerField(default=0)
    last_story_date = models.IntegerField(default=0)
    stories_last_month = models.IntegerField(default=0)
    average_stories_per_month = models.IntegerField(default=0)
    is_push = models.BooleanField(default=False)
    favicon_color = models.CharField(max_length=6, blank=True, default="")
    favicon_not_found = models.BooleanField(default=False)
    favicon_checked_at = models.IntegerField(default=0)
    created_at = models.IntegerField(default=0)

    def __str__(self):
        return self.title or self.feed_address


class FeedIcon(models.Model):
    feed = models.OneToOneField(Feed, primary_key=True, on_delete=models.CASCADE, related_name="icon")
    data = models.BinaryField()
    content_type = models.CharField(max_length=40, default="image/png")
    icon_url = models.CharField(max_length=1000, blank=True, default="")
    updated_at = models.IntegerField(default=0)


class Story(models.Model):
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="stories")
    guid = models.TextField()
    guid_hash = models.CharField(max_length=40)
    title = models.TextField(blank=True, default="")
    author = models.CharField(max_length=255, blank=True, default="")
    permalink = models.TextField(blank=True, default="")
    content = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list)
    image_urls = models.JSONField(default=list)
    story_date = models.IntegerField()
    fetched_at = models.IntegerField(default=0)
    updated_at = models.IntegerField(default=0)
    original_text = models.TextField(null=True, blank=True)
    original_text_at = models.IntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["feed", "guid_hash"], name="story_feed_guid")]
        indexes = [
            models.Index(fields=["feed", "-story_date", "-id"], name="story_feed_date"),
            models.Index(fields=["story_date", "id"], name="story_date_id"),
        ]

    def __str__(self):
        return self.title[:80]


class PushSubscription(models.Model):
    """WebSub subscription of a feed (item 4). A row with removing=True waits for the hub to
    confirm an unsubscribe and no longer belongs to a feed."""

    feed = models.OneToOneField(Feed, null=True, blank=True, on_delete=models.SET_NULL, related_name="push")
    hub = models.CharField(max_length=1000)
    topic = models.CharField(max_length=1000)
    callback_token = models.CharField(max_length=64)
    secret = models.CharField(max_length=64)
    verified = models.BooleanField(default=False)
    lease_seconds = models.BigIntegerField(default=0)
    lease_expires = models.BigIntegerField(default=0)
    callback_base = models.CharField(max_length=500, blank=True, default="")
    last_push_at = models.IntegerField(default=0)
    requested_at = models.IntegerField(default=0)
    removing = models.BooleanField(default=False)


class FeedFetchLog(models.Model):
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="fetch_log")
    type = models.CharField(max_length=8)  # "feed" or "push"
    at = models.IntegerField()
    status_code = models.IntegerField(default=0)
    message = models.CharField(max_length=255, blank=True, default="")
    duration_ms = models.IntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["feed", "type", "-id"], name="fetchlog_feed_type")]


class FeedStoryCount(models.Model):
    feed = models.ForeignKey(Feed, on_delete=models.CASCADE, related_name="story_counts")
    period_type = models.CharField(max_length=8)  # month | weekday | hour
    period_key = models.CharField(max_length=8)
    count = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["feed", "period_type", "period_key"], name="storycount_period")
        ]
