"""Admin registration for jobs (worker/admin.py)."""
from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("id", "type", "feed_id", "interactive", "state", "created_at", "finished_at", "error")
    list_filter = ("type", "state")
    exclude = ("blob",)
