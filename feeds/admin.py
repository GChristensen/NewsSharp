"""Admin registration for feed data (feeds/admin.py)."""
from django.contrib import admin

from .models import Feed, FeedFetchLog, PushSubscription, Setting


@admin.register(Feed)
class FeedAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "feed_address", "fetched_once", "has_exception", "errors_since_good", "is_push")
    search_fields = ("title", "feed_address")
    list_filter = ("has_exception", "is_push", "fetched_once")


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "feed", "hub", "verified", "lease_expires", "last_push_at", "removing")


@admin.register(FeedFetchLog)
class FeedFetchLogAdmin(admin.ModelAdmin):
    list_display = ("id", "feed", "type", "at", "status_code", "message", "duration_ms")
    list_filter = ("type", "status_code")


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ("key",)
