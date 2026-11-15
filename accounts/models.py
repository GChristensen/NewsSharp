"""User model (accounts/models.py): Django's AbstractUser plus a preferences JSON field (R22.1)."""
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    preferences = models.JSONField(default=dict, blank=True)

    def save(self, *args, **kwargs):
        # R22.2: at most 10 users, enforced when a user is saved.
        if self._state.adding and User.objects.count() >= settings.MAX_USERS:
            raise ValidationError(f"The number of users is limited to {settings.MAX_USERS}.")
        super().save(*args, **kwargs)

    @property
    def days_of_unread(self) -> int:
        value = (self.preferences or {}).get("days_of_unread", 30)
        return value if isinstance(value, int) and 1 <= value <= 365 else 30
