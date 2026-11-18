"""Checks of the NewsBlur import (tests/test_import.py)."""
import base64
import io
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from PIL import Image

from accounts.models import User
from feeds.models import Feed
from reading import tree
from reading.models import Folder, Subscription, ViewSetting

pytestmark = pytest.mark.django_db


def export_document() -> dict:
    icon = io.BytesIO()
    Image.new("RGB", (16, 16), (10, 20, 30)).save(icon, format="PNG")
    return {
        "version": 1,
        "users": [{"id": 7, "username": "greg", "is_staff": True, "date_joined": "2024-01-01T00:00:00"}],
        "feeds": [
            {"id": 100, "feed_address": "https://a.example/feed", "feed_link": "https://a.example/", "feed_title": "A"},
            {"id": 200, "feed_address": "https://b.example/feed", "feed_link": "", "feed_title": "B"},
            {"id": 300, "feed_address": "https://c.example/feed", "feed_link": "", "feed_title": "C"},
        ],
        "subscriptions": [
            {"user_id": 7, "feed_id": 100, "user_title": "My A"},
            {"user_id": 7, "feed_id": 200, "user_title": None},
            {"user_id": 7, "feed_id": 300, "user_title": None},
        ],
        "folders": {"7": [200, {"Tech": [100, {"Deep": []}, 100]}, 999]},
        "icons": [{"feed_id": 100, "data": base64.b64encode(icon.getvalue()).decode(), "color": "aabbcc", "icon_url": ""}],
        "view_settings": {"7": {"100": {"v": "page", "l": "grid", "d": "x"}, "river:Tech": {"o": "oldest"}, "river:": "feed", "starred": {"l": "list"}}},
        "preferences": {"7": {"timezone": "Europe/Berlin", "days_of_unread": 14, "feed_pane_size": 300,
                              "preferences": {"story_layout": "list", "unknown_key": 1, "grid_columns": 99},
                              "collapsed_folders": ["Deep"]}},
    }  # fmt: skip


def test_import_newsblur(tmp_path):
    source = tmp_path / "export.json"
    source.write_text(json.dumps(export_document()), encoding="utf-8")
    mapping = tmp_path / "map.json"
    call_command("import_newsblur", str(source), map=str(mapping))

    user = User.objects.get(username="greg")
    assert user.is_superuser and user.preferences == {
        "story_layout": "list", "timezone": "Europe/Berlin", "days_of_unread": 14, "feed_pane_size": 300,
    }  # fmt: skip
    feed_map = json.loads(mapping.read_text())
    assert set(feed_map) == {"100", "200", "300"} and Feed.objects.count() == 3

    def listing(parent_id=None, depth=0):
        lines = []
        for item in tree.container_items(user, parent_id):
            if isinstance(item, Folder):
                lines.append("  " * depth + item.title)
                lines.extend(listing(item.id, depth + 1))
            else:
                lines.append("  " * depth + (item.user_title or item.feed.title))
        return lines

    # depth-first listing equals the source tree; the duplicate keeps its first place; C goes to the root
    assert listing() == ["B", "Tech", "  My A", "  Deep", "C"]
    assert Folder.objects.get(title="Deep").collapsed and not Folder.objects.get(title="Tech").collapsed
    feed_a = Feed.objects.get(feed_address="https://a.example/feed")
    assert feed_a.favicon_color == "aabbcc" and bytes(feed_a.icon.data).startswith(b"\x89PNG")
    assert not feed_a.fetched_once and Subscription.objects.count() == 3
    settings = {(v.target_type, v.target_id, v.key): v.value for v in ViewSetting.objects.filter(user=user)}
    tech = Folder.objects.get(title="Tech")
    assert settings == {
        ("feed", feed_a.id, "view"): "original", ("feed", feed_a.id, "layout"): "grid",
        ("folder", tech.id, "order"): "oldest", ("all", 0, "view"): "feed", ("saved", 0, "layout"): "list",
    }  # fmt: skip
    with pytest.raises(CommandError):
        call_command("import_newsblur", str(source), map=str(mapping))
