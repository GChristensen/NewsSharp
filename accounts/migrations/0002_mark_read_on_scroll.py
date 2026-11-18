"""Renames the preference mark_read_on_scroll_titles to mark_read_on_scroll
(accounts/migrations/0002_mark_read_on_scroll.py): the setting now covers every layout, not only Split."""
from django.db import migrations

OLD = "mark_read_on_scroll_titles"
NEW = "mark_read_on_scroll"


def rename(apps, schema_editor, old=OLD, new=NEW):
    User = apps.get_model("accounts", "User")
    for user in User.objects.all():
        prefs = user.preferences or {}
        if old in prefs:
            prefs[new] = prefs.pop(old)
            user.preferences = prefs
            user.save(update_fields=["preferences"])


def rename_back(apps, schema_editor):
    rename(apps, schema_editor, old=NEW, new=OLD)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_initial")]
    operations = [migrations.RunPython(rename, rename_back)]