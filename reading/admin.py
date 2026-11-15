"""Admin registration for subscriptions and folders (reading/admin.py), R22.6."""
from django.contrib import admin

from .models import Folder, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "feed", "folder", "position", "user_title", "deleted_at")
    list_filter = ("user",)


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "parent", "position", "collapsed", "deleted_at")
    list_filter = ("user",)