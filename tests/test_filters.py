"""Checks of the story filters: validation of the Promote, Demote and Hide preferences, hidden unread
counts, ordering of story lists across pages, and the listing of hidden stories (tests/test_filters.py)."""
import json

import pytest

from accounts import prefs
from reading import stories as story_service
from reading.unread import unread_counts

pytestmark = pytest.mark.django_db


def set_filters(user, **lines):
    user.preferences = {**(user.preferences or {}), **{f"filter_{key}": value for key, value in lines.items()}}
    user.save(update_fields=["preferences"])


def titles(api, scope, **params):
    query = "&".join(f"{key}={value}" for key, value in {"scope": scope, **params}.items())
    return [story["title"] for story in api.get(f"/api/stories?{query}").json()["stories"]]


def test_filter_preferences_are_validated(api):
    assert not prefs.invalid_keys({"filter_hide": ["sponsored", r"\bads?\b", "rust|golang"]})
    for bad in (["(unclosed"], [""], [" padded "], ["a" * 501], ["x"] * 201, "sponsored", [3], ["a", "(?i)b"]):
        assert prefs.invalid_keys({"filter_hide": bad}) == ["filter_hide"], bad
    response = api.patch("/api/preferences", data=json.dumps({"filter_promote": ["[a-"]}), content_type="application/json")
    assert response.status_code == 422
    response = api.patch("/api/preferences", data=json.dumps({"filter_promote": ["python"]}), content_type="application/json")
    assert response.json()["preferences"]["filter_promote"] == ["python"]


def test_hidden_stories_leave_lists_and_counts(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    make_story(feed, title="Weekly digest", age_days=1)
    make_story(feed, title="Plain news", author="Ad Bot", age_days=2)
    make_story(feed, title="Another story", content="<p>A <b>Sponsored</b> post</p>", age_days=3)
    make_story(feed, title="Ordinary story", age_days=4)
    set_filters(user, hide=[r"\bad bot\b", "sponsored", "^weekly"])

    assert unread_counts(user) == {feed.id: {"unread": 1, "hidden": 3, "hidden_all": 3}}
    assert api.get("/api/tree").json()["feeds"][0]["hidden"] == 3
    assert titles(api, f"feed:{feed.id}") == ["Ordinary story"]
    assert titles(api, "all") == ["Ordinary story"]

    listed = api.get(f"/api/stories?scope=feed:{feed.id}&hidden=1").json()["stories"]
    assert [(s["title"], s["hidden"]) for s in listed] == [
        ("Weekly digest", True), ("Plain news", True), ("Another story", True), ("Ordinary story", False),
    ]  # fmt: skip

    assert api.get(f"/api/stories/{listed[0]['id']}").json()["hidden"] is True

    # A search lists every match; marking a hidden story read lowers the hidden count.
    assert titles(api, f"feed:{feed.id}", q="weekly") == ["Weekly digest"]
    api.post("/api/read", data=json.dumps({"story_ids": [listed[0]["id"]]}), content_type="application/json")
    assert unread_counts(user)[feed.id] == {"unread": 1, "hidden": 2, "hidden_all": 3}


def test_promoted_first_demoted_last_across_pages(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    for day, title in enumerate(["one", "promo two", "three", "meh four", "promo meh five", "six", "meh seven"]):
        make_story(feed, title=title, age_days=day + 1)
    set_filters(user, promote=["promo"], demote=["meh"], hide=["seven"])

    expected = ["promo two", "promo meh five", "one", "three", "six", "meh four"]
    assert titles(api, f"feed:{feed.id}") == expected
    assert titles(api, f"feed:{feed.id}", order="oldest") == ["promo meh five", "promo two", "six", "three", "one", "meh four"]

    seen, cursor = [], ""
    while True:
        page = api.get(f"/api/stories?scope=feed:{feed.id}&limit=2&cursor={cursor}").json()
        seen += [story["title"] for story in page["stories"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == expected


def test_saved_stories_ignore_filters(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    story = make_story(feed, title="Sponsored thing")
    story_service.save_story(user, story.id)
    set_filters(user, hide=["sponsored"])
    assert titles(api, "saved") == ["Sponsored thing"]


def patch_feed(api, feed_id, filters):
    return api.patch(f"/api/subscriptions/{feed_id}", data=json.dumps({"filters": filters}), content_type="application/json")


def test_feed_filters_come_before_the_global_ones(api, user, make_feed, make_story, subscribe):
    first, second = make_feed("https://one.example/feed.xml"), make_feed("https://two.example/feed.xml")
    subscribe(user, first)
    subscribe(user, second)
    for feed in (first, second):
        make_story(feed, title=f"rust news {feed.id}", age_days=1)
        make_story(feed, title=f"golang news {feed.id}", age_days=2)
        make_story(feed, title=f"python news {feed.id}", age_days=3)
    set_filters(user, hide=["golang"], promote=["python"])

    assert patch_feed(api, first.id, {"hide": ["(unclosed"]}).status_code == 422
    response = patch_feed(api, first.id, {"hide": ["rust"], "promote": [], "demote": []}).json()
    assert response["counts"] == {str(first.id): {"unread": 1, "hidden": 2, "hidden_all": 2}}  # own line + global
    assert unread_counts(user)[second.id] == {"unread": 2, "hidden": 1, "hidden_all": 1}  # the global line alone

    feeds = {f["id"]: f for f in api.get("/api/tree").json()["feeds"]}
    assert feeds[first.id]["filters"] == {"promote": [], "demote": [], "hide": ["rust"]}
    assert feeds[second.id]["filters"] == {"promote": [], "demote": [], "hide": []}

    assert titles(api, f"feed:{first.id}") == [f"python news {first.id}"]
    assert titles(api, f"feed:{second.id}") == [f"python news {second.id}", f"rust news {second.id}"]
    patch_feed(api, second.id, {"demote": ["python"]})
    # The feed's Demote line does not override the global Promote: Promote wins over Demote.
    assert titles(api, f"feed:{second.id}") == [f"python news {second.id}", f"rust news {second.id}"]
    patch_feed(api, second.id, {"promote": ["rust"]})
    assert titles(api, "all") == [f"rust news {second.id}", f"python news {second.id}", f"python news {first.id}"]


def test_hidden_count_covers_only_the_unread_stories_shown(api, user, make_feed, make_story, subscribe):
    feed = make_feed()
    subscribe(user, feed)
    make_story(feed, title="ad old", age_days=5)
    old = make_story(feed, title="old news", age_days=4)
    make_story(feed, title="ad mid", age_days=3)
    make_story(feed, title="new news", age_days=1)
    make_story(feed, title="ad new", age_days=0.5)
    set_filters(user, hide=[r"^ad\b"])
    # "ad old" is older than every visible unread story, so it is not among them.
    assert unread_counts(user)[feed.id] == {"unread": 2, "hidden": 2, "hidden_all": 3}

    # Reading past a hidden story takes it out of the count, though it was never opened.
    api.post("/api/read", data=json.dumps({"story_ids": [old.id]}), content_type="application/json")
    assert unread_counts(user)[feed.id] == {"unread": 1, "hidden": 1, "hidden_all": 3}
    assert titles(api, f"feed:{feed.id}", hidden=1, filter="unread") == ["ad new", "new news"]
    assert titles(api, f"feed:{feed.id}", hidden=1, filter="all") == ["ad new", "new news", "ad mid", "old news", "ad old"]

    # Nothing visible left unread: no hidden story counts or is listed under Unread.
    visible = [story["id"] for story in api.get(f"/api/stories?scope=feed:{feed.id}").json()["stories"]]
    api.post("/api/read", data=json.dumps({"story_ids": visible}), content_type="application/json")
    assert unread_counts(user)[feed.id] == {"unread": 0, "hidden": 0, "hidden_all": 3}
    assert titles(api, f"feed:{feed.id}", hidden=1, filter="unread") == []
