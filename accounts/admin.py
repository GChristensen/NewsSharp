"""Admin registration for users (accounts/admin.py). The admin is for superusers only."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User

admin.site.site_header = "NewsSharp administration"
admin.site.site_title = "NewsSharp"
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser


@admin.register(User)
class NewsSharpUserAdmin(UserAdmin):
    list_display = ("username", "is_superuser", "is_active", "last_login", "date_joined")
    fieldsets = UserAdmin.fieldsets + (("Reader", {"fields": ("preferences",)}),)
