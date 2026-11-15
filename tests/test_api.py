"""Acceptance checks of items 4, 6, 7, 10, 12, 15, 17, 19, 21 and 22 through the HTTP API
(tests/test_api.py)."""
import hashlib
import hmac
import json

import pytest

from accounts.models import User
from api.events import bus
from feeds.models import Feed, FeedFetchLog, PushSubscription, Setting, Story
from feeds.util import image_signature, now
from reading import stories as story_service
from reading import tree
from reading.models import Folder, Subscription
from worker import coordinator, pipeline, websub
from worker.models import Job

pytestmark = pytest.mark.django_db


def post(client, path, data=None):
    return client.post(path, data=json.dumps(data or {}), content_type="application/json")


def counts(client):
    return client.get("/api/counts").json()["feeds"]


# ------------------------------------------------------------------ session (item 22)


def test_unauthenticated_requests_are_rejected(client):
    assert client.get("/api/tree").status_code == 401
    response = client.get("/api/bootstrap")
    assert response.status_code == 401 and response.json()["needs_setup"] is True


def test_setup_login_and_public_url(client):
    response = post(client, "/api/setup", {"username": "admin", "password": "long-enough", "timezone": "Europe/Berlin"})
    assert response.status_code == 200
    user = User.objects.get(username="admin")
    assert user.is_superuser and user.preferences["timezone"] == "Europe/Berlin"
    assert Setting.get("public_base_url") == "http://testserver"
    assert post(client, "/api/setup", {"username": "x", "password": "long-enough"}).status_code == 409
    boot = client.get("/api/bootstrap").json()
    assert boot["user"]["username"] == "admin" and boot["preferences"]["theme"] == "dark"
    post(client, "/api/logout")
    assert client.get("/api/tree").status_code == 401
    assert post(client, "/api/login", {"username": "admin", "password": "wrong"}).status_code == 401
    assert post(client, "/api/login", {"username": "admin", "password": "long-enough"}).status_code == 200


def test_login_is_throttled(client, user):
    for _ in range(10):
        post(client, "/api/login", {"username": "alice", "password": "wrong"})
    assert post(client, "/api/login", {"username": "alice", "password": "secret-password"}).status_code == 429


def test_user_limit(db):
    for i in range(10):
        User.objects.create_user(username=f"user{i}", password="x")
    with pytest.raises(Exception):
        User.objects.create_user(username="eleventh", password="x")


def test_csrf_is_enforced(user):
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    assert strict.post("/api/read", data="{}", content_type="application/json").status_code == 403


# ------------------------------------------------------------------ unread state (item 6)


def test_unread_counts_and_marking(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    stories = [make_story(feed, age_days=5 - i) for i in range(5)]  # oldest first
    assert counts(api) == {str(feed.id): 5}
    response = post(api, "/api/read", {"story_ids": [stories[0].id, stories[1].id]})
    assert response.json()["counts"] == {str(feed.id): 3}
    post(api, "/api/read", {"story_ids": [stories[0].id]})  # idempotent
    assert counts(api) == {str(feed.id): 3}
    post(api, "/api/mark-read", {"scope": f"feed:{feed.id}"})
    assert counts(api) == {str(feed.id): 0}
    sub = Subscription.objects.get(user=user, feed=feed)
    assert sub.mark_read_date == stories[4].story_date + 1
    make_story(feed, age_days=2.5)  # fetched later with an older date: stays read
    assert counts(api) == {str(feed.id): 0}


def test_mark_unread_moves_the_read_date(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    stories = [make_story(feed, age_days=5 - i) for i in range(5)]
    post(api, "/api/mark-read", {"scope": f"feed:{feed.id}"})
    assert post(api, "/api/unread", {"story_id": stories[2].id}).json()["counts"] == {str(feed.id): 1}
    listed = api.get(f"/api/stories?scope=feed:{feed.id}&filter=unread").json()["stories"]
    assert [s["id"] for s in listed] == [stories[2].id]


def test_mark_unread_refuses_old_stories(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    old = make_story(feed, age_days=40)
    response = post(api, "/api/unread", {"story_id": old.id})
    assert response.status_code == 409 and response.json()["message"] == "Story is more than 30 days old"


def test_mark_older_than_story(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    stories = [make_story(feed, age_days=5 - i) for i in range(5)]
    post(api, "/api/mark-read", {"scope": f"feed:{feed.id}", "cutoff": stories[2].story_date, "direction": "older"})
    assert counts(api) == {str(feed.id): 2}
    post(api, "/api/mark-read", {"scope": f"feed:{feed.id}", "cutoff": stories[4].story_date, "direction": "newer"})
    assert counts(api) == {str(feed.id): 1}


def test_undo_mark_feed_read(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    stories = [make_story(feed, age_days=5 - i) for i in range(5)]
    post(api, "/api/read", {"story_ids": [stories[0].id]})
    before = Subscription.objects.get(user=user, feed=feed).mark_read_date
    token = post(api, "/api/mark-read", {"scope": f"feed:{feed.id}"}).json()["undo_token"]
    assert counts(api) == {str(feed.id): 0}
    response = post(api, "/api/mark-read/undo", {"undo_token": token}).json()
    assert response["counts"] == {str(feed.id): 4}
    assert sorted(response["story_ids"]) == sorted(s.id for s in stories[1:])
    assert Subscription.objects.get(user=user, feed=feed).mark_read_date == before
    assert post(api, "/api/mark-read/undo", {"undo_token": token}).status_code == 410  # already undone


def test_undo_mark_folder_read_skips_feeds_changed_since(api, user, make_feed, make_story, subscribe):
    folder = tree.create_folder(user, "News", None)
    feed_a, feed_b = make_feed("https://a.example.com/feed.xml"), make_feed("https://b.example.com/feed.xml")
    subscribe(user, feed_a, folder_id=folder.id)
    subscribe(user, feed_b, folder_id=folder.id)
    a = [make_story(feed_a, age_days=3 - i) for i in range(3)]
    make_story(feed_b, age_days=1)
    token = post(api, "/api/mark-read", {"scope": f"folder:{folder.id}"}).json()["undo_token"]
    post(api, "/api/unread", {"story_id": a[1].id})  # moves feed_a's read date: its undo no longer applies
    assert post(api, "/api/mark-read/undo", {"undo_token": token}).status_code == 200
    assert counts(api) == {str(feed_a.id): 1, str(feed_b.id): 1}


def test_mark_read_undo_only_whole_feeds(api, user, other_user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    story = make_story(feed, age_days=2)
    assert "undo_token" not in post(api, "/api/mark-read", {"scope": "all"}).json()
    assert "undo_token" not in post(api, "/api/mark-read", {"scope": f"feed:{feed.id}", "days": 1}).json()
    older = {"scope": f"feed:{feed.id}", "cutoff": story.story_date, "direction": "older"}
    assert "undo_token" not in post(api, "/api/mark-read", older).json()
    assert post(api, "/api/mark-read", {"scope": f"feed:{feed.id}"}).json()["undo_token"] is None  # nothing moved
    assert post(api, "/api/mark-read/undo", {"undo_token": "forged"}).status_code == 410
    subscribe(other_user, feed)
    from reading.unread import mark_read_undo_token, undo_mark_read

    sub = Subscription.objects.get(user=other_user, feed=feed)
    token = mark_read_undo_token(other_user, [sub], {feed.id: sub.mark_read_date + 10})
    assert post(api, "/api/mark-read/undo", {"undo_token": token}).status_code == 410  # another user's
    assert undo_mark_read(other_user, token) is None  # the recorded date is not the current one


def test_users_do_not_share_read_state(api, user, other_user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    subscribe(other_user, feed)
    story = make_story(feed)
    assert Feed.objects.count() == 1
    post(api, "/api/read", {"story_ids": [story.id]})
    from reading.unread import unread_counts

    assert unread_counts(user) == {feed.id: 0} and unread_counts(other_user) == {feed.id: 1}


# ------------------------------------------------------------------ tree (item 7)


def test_tree_move_delete_and_undo(api, user, make_feed, make_story, subscribe):
    folder_a = post(api, "/api/folders", {"title": "A"}).json()
    folder_b = post(api, "/api/folders", {"title": "B", "parent_id": folder_a["id"]}).json()
    feeds = [make_feed(f"https://example.com/{i}.xml") for i in range(4)]
    subscribe(user, feeds[0], folder_b["id"])
    subscribe(user, feeds[1], folder_b["id"])
    subscribe(user, feeds[2], folder_a["id"])
    subscribe(user, feeds[3])
    make_story(feeds[0])
    make_story(feeds[2])

    # a feed between the two feeds of nested folder B
    assert post(api, "/api/tree/move", {"item": f"feed:{feeds[3].id}", "parent_id": folder_b["id"], "position": 1}).status_code == 200
    order = [i.feed_id for i in tree.container_items(user, folder_b["id"])]
    assert order == [feeds[0].id, feeds[3].id, feeds[1].id]

    # a folder cannot be moved into its own subfolder
    response = post(api, "/api/tree/move", {"item": f"folder:{folder_a['id']}", "parent_id": folder_b["id"], "position": 0})
    assert response.status_code == 422

    before = api.get("/api/tree").json()
    token = api.delete(f"/api/folders/{folder_a['id']}").json()["undo_token"]
    assert api.get("/api/tree").json()["feeds"] == [] and api.get("/api/tree").json()["folders"] == []
    restored = post(api, "/api/undo", {"undo_token": token}).json()
    assert restored["feeds"] == before["feeds"] and restored["folders"] == before["folders"]


def test_folder_counts_and_purge(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    make_story(feed)
    api.delete(f"/api/subscriptions/{feed.id}")
    Subscription.objects.update(deleted_at=now() - 120)
    Feed.objects.update(created_at=now() - 120)
    coordinator.purge_deleted()
    assert Feed.objects.count() == 0 and Story.objects.count() == 0


def test_rename_and_restore_title(api, user, make_feed, subscribe):
    feed = make_feed(title="Original")
    subscribe(user, feed)
    api.patch(f"/api/subscriptions/{feed.id}", data=json.dumps({"title": "Mine"}), content_type="application/json")
    assert api.get("/api/tree").json()["feeds"][0]["title"] == "Mine"
    api.patch(f"/api/subscriptions/{feed.id}", data=json.dumps({"title": ""}), content_type="application/json")
    assert api.get("/api/tree").json()["feeds"][0]["title"] == "Original"


# ------------------------------------------------------------------ stories, paging, search


def test_keyset_paging(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    for i in range(95):
        make_story(feed, story_date=now() - 1000 - i)
    seen, cursor, sizes = [], "", []
    while True:
        page = api.get(f"/api/stories?scope=feed:{feed.id}&cursor={cursor}").json()
        sizes.append(len(page["stories"]))
        seen.extend(s["id"] for s in page["stories"])
        if cursor == "" :
            make_story(feed, story_date=now())  # arrives while reading: no duplicate, no skipped story
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sizes == [30, 30, 30, 5] and len(set(seen)) == 95
    oldest = api.get(f"/api/stories?scope=feed:{feed.id}&order=oldest&limit=1").json()["stories"][0]
    assert oldest["id"] == seen[-1]


def test_search(api, user, make_feed, make_story, subscribe):
    feed, other = make_feed(), make_feed("https://example.com/other.xml")
    folder = tree.create_folder(user, "Tech")
    subscribe(user, feed, folder.id)
    subscribe(user, other)
    both = make_story(feed, title="Linux kernel released", content="<p>The new kernel for linux is out.</p>")
    split = make_story(feed, title="Kernel of truth", content="<p>Running linux everywhere, a résumé.</p>")
    title_only = make_story(other, title="Popcorn", content="<p>about a linux kernel</p>")

    def search(q, scope="all"):
        return [s["id"] for s in api.get("/api/stories", {"scope": scope, "q": q}).json()["stories"]]

    assert set(search("linux kernel")) == {both.id, split.id, title_only.id}
    assert set(search('"linux kernel"')) == {both.id, title_only.id}
    assert set(search("linux -popcorn")) == {both.id, split.id}
    assert set(search("title:linux")) == {both.id}
    assert search("resume") == [split.id] and search("run") == [split.id]
    assert set(search('"unbalanced')) == set() and set(search('"linux')) == {both.id, split.id, title_only.id}
    assert set(search("linux", f"folder:{folder.id}")) == {both.id, split.id}
    pipeline.store.delete_stories([both.id])
    assert both.id not in search("linux")


def test_saved_for_later(api, user, make_feed, make_story, subscribe):
    feed = make_feed(title="Feed title")
    subscribe(user, feed)
    story = make_story(feed, title="Keep me")
    saved = post(api, "/api/saved", {"story_id": story.id, "user_tags": ["later", "tech"]}).json()
    assert saved["saved"] and saved["counts"]["total"] == 1 and saved["counts"]["tags"] == {"later": 1, "tech": 1}
    post(api, "/api/saved", {"story_id": story.id})  # saving again is a no-op
    assert story_service.saved_counts(user)["total"] == 1
    assert api.get(f"/api/stories?scope=feed:{feed.id}").json()["stories"][0]["saved"] is True
    api.delete(f"/api/subscriptions/{feed.id}")
    listed = api.get("/api/stories?scope=saved").json()["stories"]
    assert listed[0]["title"] == "Keep me" and listed[0]["feed_title"] == "Feed title"
    assert [s["title"] for s in api.get("/api/stories", {"scope": "saved:tag:later"}).json()["stories"]] == ["Keep me"]
    assert [s["title"] for s in api.get("/api/stories", {"scope": "saved", "q": "keep"}).json()["stories"]] == ["Keep me"]
    assert api.delete(f"/api/saved/{saved['saved_id']}").json()["counts"]["total"] == 0


def test_image_signatures_are_delivered(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    make_story(feed, content='<p><img src="http://img.example/a.png"></p>', image_urls=["http://img.example/a.png"])
    story_service._image_secret = None  # the per-process cache must not outlive the test database
    story = api.get(f"/api/stories?scope=feed:{feed.id}").json()["stories"][0]
    secret = Setting.secret("image_secret")
    assert story["image_sigs"] == {"http://img.example/a.png": image_signature("http://img.example/a.png", secret)}


# ------------------------------------------------------------------ settings (item 19)


def test_preferences_and_view_settings(api, user, make_feed, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    bad = api.patch("/api/preferences", data=json.dumps({"grid_columns": 9}), content_type="application/json")
    assert bad.status_code == 422
    good = api.patch("/api/preferences", data=json.dumps({"grid_columns": 3}), content_type="application/json")
    assert good.json()["preferences"]["grid_columns"] == 3
    put = api.put("/api/view-settings", data=json.dumps({"scope": f"feed:{feed.id}", "key": "layout", "value": "grid"}),
                  content_type="application/json")  # fmt: skip
    assert put.json()["view_settings"] == {f"feed:{feed.id}": {"layout": "grid"}}
    assert api.get("/api/bootstrap").json()["view_settings"][f"feed:{feed.id}"]["layout"] == "grid"
    api.delete("/api/view-settings", data=json.dumps({"scope": f"feed:{feed.id}", "key": "layout"}), content_type="application/json")
    assert api.get("/api/bootstrap").json()["view_settings"] == {}


def test_grid_columns_per_scope(api, user, make_feed, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    assert api.get("/api/bootstrap").json()["preferences"]["grid_columns"] == 4
    scope = f"feed:{feed.id}"
    put = api.put("/api/view-settings", data=json.dumps({"scope": scope, "key": "grid_columns", "value": "2"}),
                  content_type="application/json")  # fmt: skip
    assert put.json()["view_settings"] == {scope: {"grid_columns": "2"}}
    bad = api.put("/api/view-settings", data=json.dumps({"scope": scope, "key": "grid_columns", "value": "7"}),
                  content_type="application/json")  # fmt: skip
    assert bad.status_code == 422


def test_story_title_previews_per_scope(api, user, make_feed, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    scope = f"feed:{feed.id}"

    def put(key, value):
        return api.put("/api/view-settings", data=json.dumps({"scope": scope, "key": key, "value": value}),
                       content_type="application/json")  # fmt: skip

    put("show_content_preview", "title")
    stored = put("image_preview", "large-right").json()["view_settings"]
    assert stored == {scope: {"show_content_preview": "title", "image_preview": "large-right"}}
    assert put("image_preview", "huge").status_code == 422
    prefs = api.get("/api/bootstrap").json()["preferences"]
    assert prefs["show_content_preview"] == "medium" and prefs["image_preview"] == "small-left"  # defaults kept


# ------------------------------------------------------------------ OPML (item 15)

OPML = b"""<?xml version="1.0"?><opml version="2.0"><head><title>Feeds</title></head><body>
<outline text="Tech" title="Tech"><outline text="Kernel" title="Kernel">
<outline type="rss" text="LWN" title="LWN" xmlUrl="https://lwn.net/headlines/rss" htmlUrl="https://lwn.net/"/>
</outline></outline>
<outline type="rss" text="Example" title="Example" xmlUrl="https://example.com/feed.xml"/>
<outline type="rss" text="Again" xmlUrl="https://lwn.net/headlines/rss"/>
<outline type="rss" text="Broken" xmlUrl="not a url"/>
</body></opml>"""


def test_opml_import_and_export(api, user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    result = api.post("/api/opml", {"file": SimpleUploadedFile("feeds.opml", OPML)}).json()
    assert result["feeds_added"] == 2 and result["folders_added"] == 2
    assert [s["url"] for s in result["skipped"]] == ["not a url"]
    kernel = Folder.objects.get(title="Kernel")
    assert kernel.parent.title == "Tech"
    assert Subscription.objects.get(feed__feed_address="https://lwn.net/headlines/rss").folder_id == kernel.id
    assert Job.objects.filter(type="schedule_now").exists()
    again = api.post("/api/opml", {"file": SimpleUploadedFile("feeds.opml", OPML)}).json()
    assert again["feeds_added"] == 0 and again["folders_added"] == 0 and again["feeds_existing"] == 2
    exported = api.get("/api/opml")
    assert exported["Content-Type"] == "text/x-opml" and b'xmlUrl="https://lwn.net/headlines/rss"' in exported.content
    # export -> delete everything -> import restores the tree
    before = [(f["title"], f["position"]) for f in api.get("/api/tree").json()["feeds"]]
    Subscription.objects.all().delete()
    Folder.objects.all().delete()
    api.post("/api/opml", {"file": SimpleUploadedFile("feeds.opml", exported.content)})
    assert [(f["title"], f["position"]) for f in api.get("/api/tree").json()["feeds"]] == before


def test_opml_upload_with_csrf_checks(user):
    """With CSRF checks on, the middleware consumes the multipart stream before the view runs."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client

    strict = Client(enforce_csrf_checks=True)
    strict.force_login(user)
    strict.get("/api/bootstrap")  # sets the csrftoken cookie
    token = strict.cookies["csrftoken"].value
    response = strict.post("/api/opml", {"file": SimpleUploadedFile("feeds.opml", OPML)}, headers={"X-CSRFToken": token})
    assert response.status_code == 200 and response.json()["feeds_added"] == 2


# ------------------------------------------------------------------ WebSub (item 4)


@pytest.fixture
def push(make_feed):
    feed = make_feed(fetched_once=True)
    Setting.put("public_base_url", "http://reader:8123")
    request = websub.plan(feed.id, "https://hub.example/", "https://example.com/feed.xml", False)
    assert request["action"] == "subscribe" and request["base"] == "http://reader:8123" and len(request["secret"]) == 64
    return PushSubscription.objects.get(pk=request["id"])


def verify(client, push, **overrides):
    params = {"hub.mode": "subscribe", "hub.topic": push.topic, "hub.challenge": "abc123", "hub.lease_seconds": "2592000"}
    params.update(overrides)
    return client.get(f"/push/{push.id}/{overrides.pop('token', push.callback_token)}", params)


def test_push_verification(client, push):
    assert verify(client, push, **{"hub.topic": "https://wrong.example/"}).status_code == 404
    assert client.get(f"/push/{push.id}/wrong-token", {"hub.mode": "subscribe", "hub.topic": push.topic, "hub.challenge": "x"}).status_code == 404
    assert client.get(f"/push/999/{push.callback_token}").status_code == 404
    push.refresh_from_db()
    assert not push.verified
    response = verify(client, push)
    assert response.status_code == 200 and response.content == b"abc123" and response["Content-Type"].startswith("text/plain")
    push.refresh_from_db()
    assert push.verified and push.lease_seconds == 2592000 and Feed.objects.get(pk=push.feed_id).is_push
    # a lease of 30 days is renewed 7 days before it ends
    assert not websub.lease_nearly_over(push, now())
    assert websub.lease_nearly_over(push, push.lease_expires - 7 * 86400 + 60)
    assert not websub.lease_nearly_over(push, push.lease_expires - 8 * 86400)


def test_push_notifications(client, push, user, subscribe):
    verify(client, push)
    body = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title><item><guid>pushed</guid>'
        b"<title>Pushed story</title><description>Body</description></item></channel></rss>"
    )
    url = f"/push/{push.id}/{push.callback_token}"

    def sign(method):
        return f"{method}=" + hmac.new(push.secret.encode(), body, getattr(hashlib, method)).hexdigest()

    assert client.post(url, data=body, content_type="application/rss+xml").status_code == 202
    assert client.post(url, data=body, content_type="application/rss+xml", headers={"X-Hub-Signature": "sha1=00"}).status_code == 202
    assert Job.objects.count() == 0
    assert FeedFetchLog.objects.filter(feed_id=push.feed_id, type="push", message="Bad signature").count() == 2
    for method in ("sha1", "sha256"):
        response = client.post(url, data=body, content_type="application/rss+xml", headers={"X-Hub-Signature": sign(method)})
        assert response.status_code == 202
    jobs = list(Job.objects.filter(type="push_body"))
    assert len(jobs) == 2 and bytes(jobs[0].blob) == body and not jobs[0].interactive
    # the worker processes the body through the pipeline, with no request to the feed address
    outcome = pipeline.process_feed(push.feed_id, {"kind": "push", "body": body, "content_type": "application/rss+xml"})
    assert outcome["new"] == 1 and Story.objects.get(feed_id=push.feed_id).title == "Pushed story"
    empty = pipeline.process_feed(push.feed_id, {"kind": "push", "body": b"", "content_type": ""})
    assert empty == {"fetch_needed": True}


def test_changed_hub_resubscribes_and_missing_hub_unsubscribes(client, push):
    verify(client, push)
    changed = websub.plan(push.feed_id, "https://other-hub.example/", push.topic, False)
    assert changed["action"] == "subscribe" and changed["hub"] == "https://other-hub.example/"
    gone = websub.plan(push.feed_id, None, None, False)
    assert gone["action"] == "unsubscribe"
    push.refresh_from_db()
    assert push.removing and push.feed_id is None
    response = client.get(f"/push/{push.id}/{push.callback_token}", {"hub.mode": "unsubscribe", "hub.topic": push.topic, "hub.challenge": "bye"})
    assert response.content == b"bye" and not PushSubscription.objects.exists()


# ------------------------------------------------------------------ events (item 21)


def test_events_are_per_user_and_replayable(api, user, other_user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    story = make_story(feed)
    start = bus.next_id
    post(api, "/api/read", {"story_ids": [story.id]})
    events = [(name, json.loads(data)) for _, name, data in bus.rings[user.id] if _ >= start]
    assert ("stories:read", {"ids": [story.id]}) in events
    assert ("counts", {"feeds": {str(feed.id): 0}}) in events
    assert not [item for item in bus.rings.get(other_user.id, []) if item[0] >= start]
    _, backlog = bus.register(user.id, _FakeLoop(), start - 1)
    assert [name for _, name, _ in backlog][:2] == ["stories:read", "counts"]
    _, stale = bus.register(user.id, _FakeLoop(), 5)  # an id from before this process started
    assert [name for _, name, _ in stale] == ["resync"]


class _FakeLoop:
    def call_soon_threadsafe(self, fn, *args):
        fn(*args)


def test_statistics(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    make_story(feed)
    pipeline.rebuild_counters(feed.id)
    stats = api.get(f"/api/feeds/{feed.id}/statistics").json()
    assert stats["story_count"] == 1 and sum(stats["hours"]) == 1 and sum(stats["weekdays"]) == 1
    assert len(stats["months"]) == 1
