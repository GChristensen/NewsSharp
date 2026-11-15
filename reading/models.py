"""User-side data model (reading/models.py), item 1 of the requirements. Times are UTC integer seconds."""
from django.conf import settings
from django.db import models


class Folder(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="folders")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="children")
    title = models.CharField(max_length=255)
    position = models.IntegerField(default=0)
    collapsed = models.BooleanField(default=False)
    deleted_at = models.IntegerField(null=True, blank=True)  # soft delete with undo, R7.10

    def __str__(self):
        return self.title


class Subscription(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions")
    feed = models.ForeignKey("feeds.Feed", on_delete=models.CASCADE, related_name="subscriptions")
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.SET_NULL, related_name="feeds")
    position = models.IntegerField(default=0)
    user_title = models.CharField(max_length=255, null=True, blank=True)
    mark_read_date = models.IntegerField(default=0)
    # A muted feed stays in the tree but is left out of All Site Stories, folders and their counts.
    muted = models.BooleanField(default=False)
    created_at = models.IntegerField(default=0)
    deleted_at = models.IntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "feed"], name="subscription_user_feed")]


class ReadStory(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    story_id = models.BigIntegerField()
    feed_id = models.BigIntegerField()
    story_date = models.IntegerField()
    read_at = models.IntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "story_id"], name="readstory_user_story")]
        indexes = [models.Index(fields=["user", "feed_id"], name="readstory_user_feed")]


class SavedStory(models.Model):
    """A copy of a story saved for later (R10.10). feed_id is kept after the feed is gone."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_stories")
    feed_id = models.BigIntegerField(null=True, blank=True)
    feed_title = models.CharField(max_length=255, blank=True, default="")
    guid_hash = models.CharField(max_length=40)
    title = models.TextField(blank=True, default="")
    author = models.CharField(max_length=255, blank=True, default="")
    permalink = models.TextField(blank=True, default="")
    content = models.TextField(blank=True, default="")
    tags = models.JSONField(default=list)
    image_urls = models.JSONField(default=list)
    story_date = models.IntegerField()
    saved_at = models.IntegerField()
    user_tags = models.JSONField(default=list)
    original_text = models.TextField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "feed_id", "guid_hash"], name="savedstory_user_feed_guid")
        ]
        indexes = [models.Index(fields=["user", "-saved_at", "-id"], name="savedstory_user_saved")]


class ViewSetting(models.Model):
    """Per-scope view settings (item 19). target_type is feed, folder, all or saved; target_id is 0
    for all and saved."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    target_type = models.CharField(max_length=8)
    target_id = models.BigIntegerField(default=0)
    key = models.CharField(max_length=16)
    value = models.CharField(max_length=16)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "target_type", "target_id", "key"], name="viewsetting_target")
        ]
