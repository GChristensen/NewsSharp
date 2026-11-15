"""Tests of the purge_feeds command (tests/test_purge.py)."""
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from accounts.models import User
from feeds import store
from feeds.models import Feed, Setting, Story
from feeds.util import now
from reading.models import Folder, ReadStory, SavedStory, Subscription


def fts_rows(table):
    with connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


@pytest.fixture
def populated(user, make_feed, make_story, subscribe):
    folder = Folder.objects.create(user=user, title="News")
    feed = make_feed()
    story = make_story(feed)
    subscribe(user, feed, folder.id)
    ReadStory.objects.create(user=user, story_id=story.id, feed_id=feed.id, story_date=story.story_date, read_at=now())
    saved = SavedStory.objects.create(user=user, feed_id=feed.id, guid_hash=story.guid_hash, title=story.title,
                                      story_date=story.story_date, saved_at=now())  # fmt: skip
    store.index_saved(saved.id, saved.title, "", "", [], [])


def test_purge_keeps_saved_stories_folders_and_users(populated):
    call_command("purge_feeds", yes=True)
    assert not Feed.objects.exists()
    assert not Story.objects.exists()
    assert not Subscription.objects.exists()
    assert not ReadStory.objects.exists()
    assert fts_rows("stories_fts") == 0
    assert SavedStory.objects.count() == 1
    assert fts_rows("saved_fts") == 1
    assert Folder.objects.count() == 1
    assert User.objects.count() == 1


def test_purge_with_saved_and_folders(populated):
    call_command("purge_feeds", yes=True, saved=True, folders=True)
    assert not SavedStory.objects.exists()
    assert fts_rows("saved_fts") == 0
    assert not Folder.objects.exists()
    assert User.objects.count() == 1


def test_purge_refuses_while_worker_runs(populated):
    Setting.put("worker_heartbeat", str(now()))
    with pytest.raises(CommandError):
        call_command("purge_feeds", yes=True)
    assert Feed.objects.count() == 1
    call_command("purge_feeds", yes=True, force=True)
    assert not Feed.objects.exists()
