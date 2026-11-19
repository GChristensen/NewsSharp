"""Shared test fixtures (tests/conftest.py)."""
import pytest

from accounts.models import User
from feeds import store
from feeds.models import Feed, Story
from feeds.util import guid_hash, now, story_summary
from reading import tree


@pytest.fixture
def user(db):
    return User.objects.create_user(username="alice", password="secret-password")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(username="bob", password="secret-password")


@pytest.fixture
def make_feed(db):
    def make(address="https://example.com/feed.xml", **fields):
        feed, _ = store.get_or_create_feed(address, fields.pop("title", "Example"))
        if fields:
            Feed.objects.filter(pk=feed.pk).update(**fields)
            feed.refresh_from_db()
        return feed

    return make


@pytest.fixture
def make_story(db):
    counter = {"n": 0}

    def make(feed, age_days=1.0, title=None, content="<p>Some content</p>", **fields):
        counter["n"] += 1
        guid = fields.pop("guid", f"guid-{counter['n']}")
        story = Story.objects.create(
            feed=feed, guid=guid, guid_hash=guid_hash(guid), title=title or f"Story {counter['n']}",
            content=content, summary=story_summary(content), permalink=f"https://example.com/{counter['n']}",
            story_date=fields.pop("story_date", now() - int(age_days * 86400)), fetched_at=now(), updated_at=now(),
            **fields,
        )  # fmt: skip
        store.index_story(story.id, story.title, story.content, story.author, story.tags)
        return story

    return make


@pytest.fixture
def subscribe(db):
    def do(user, feed, folder_id=None, **kwargs):
        return tree.subscribe(user, feed, folder_id, **kwargs)[0]

    return do


@pytest.fixture
def api(client, user):
    client.force_login(user)
    return client
