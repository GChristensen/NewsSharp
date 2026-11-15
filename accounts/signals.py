"""Session audit log (accounts/signals.py): every login, logout and deleted session is logged with the
code path that caused it, so an unexpected logout can be traced from the server's output."""
import logging
import os
import traceback

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.contrib.sessions.models import Session
from django.db.models.signals import pre_delete
from django.dispatch import receiver

log = logging.getLogger("newssharp.sessions")


def _caller() -> str:
    """The innermost frames of this project and of django.contrib (auth, sessions), newest first."""
    project = str(settings.BASE_DIR).replace("\\", "/")
    frames = []
    for frame in traceback.extract_stack()[:-2]:
        path = frame.filename.replace("\\", "/")
        if path == __file__.replace("\\", "/"):
            continue
        if "/django/contrib/" in path or (path.startswith(project) and "/.venv/" not in path):
            frames.append(f"{os.path.basename(path)}:{frame.lineno} {frame.name}")
    return " <- ".join(reversed(frames[-6:])) or "-"


@receiver(pre_delete, sender=Session)
def session_deleted(sender, instance, **kwargs):
    log.warning("session %s... deleted (expires %s) via %s", instance.session_key[:8], instance.expire_date, _caller())


@receiver(user_logged_in)
def logged_in(sender, request, user, **kwargs):
    log.info("login: %s from %s", user.username, request.META.get("REMOTE_ADDR", "-"))


@receiver(user_logged_out)
def logged_out(sender, request, user, **kwargs):
    log.warning("logout: %s via %s", getattr(user, "username", "-"), _caller())


@receiver(user_login_failed)
def login_failed(sender, credentials, request=None, **kwargs):
    log.warning("failed login for %r", credentials.get("username"))
