"""The contract between the web and worker processes (worker/models.py): jobs and worker events."""
from django.db import models

INTERACTIVE_TYPES = ("discover", "extract_text")


class Job(models.Model):
    type = models.CharField(max_length=32)
    feed_id = models.BigIntegerField(null=True, blank=True)
    user_id = models.BigIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict)
    blob = models.BinaryField(null=True, blank=True)  # pushed bodies
    interactive = models.BooleanField(default=False)
    state = models.CharField(max_length=8, default="queued")  # queued | running | done | failed
    created_at = models.IntegerField()
    started_at = models.IntegerField(null=True, blank=True)
    finished_at = models.IntegerField(null=True, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.CharField(max_length=500, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["state", "-interactive", "id"], name="job_state")]


class WorkerEvent(models.Model):
    """Facts about feeds reported by the worker: feed_fetched, new_stories, icon, push_state,
    feeds_purged, tree_changed."""

    at = models.IntegerField()
    type = models.CharField(max_length=16)
    feed_id = models.BigIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict)
