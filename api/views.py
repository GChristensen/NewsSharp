"""Synchronous API views (api/views.py): session, tree, read state, saved stories, story lists,
settings, OPML, statistics. Each runs in a request thread (R0.32) and never calls the network (R0.6)."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Length
from django.http import HttpResponse, HttpResponseNotModified
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts import prefs
from feeds.models import Feed, FeedFetchLog, FeedIcon, FeedStoryCount, PushSubscription, Setting, Story
from feeds.util import now
from reading import opml, stories, tree, unread
from reading.models import Subscription, ViewSetting
from worker.models import Job

from . import background, jobs
from .events import bus
from .http import ApiError, client_id, endpoint, error_response, require_int, require_str

User = get_user_model()

# ------------------------------------------------------------------ helpers


def publish(request, event: str, data: dict) -> None:
    bus.publish(request.user.id, event, data, origin=client_id(request))


def publish_tree(request) -> None:
    publish(request, "tree", {"version": now()})


def view_settings_payload(user) -> dict:
    result: dict[str, dict] = {}
    for target_type, target_id, key, value in ViewSetting.objects.filter(user=user).values_list(
        "target_type", "target_id", "key", "value"
    ):
        scope = target_type if target_type in ("all", "saved") else f"{target_type}:{target_id}"
        result.setdefault(scope, {})[key] = value
    return result


def scope_target(scope: str) -> tuple[str, int]:
    parts = (scope or "").split(":")
    if parts[0] in ("all", "saved"):
        return parts[0], 0
    if parts[0] in ("feed", "folder") and len(parts) == 2 and parts[1].isdigit():
        return parts[0], int(parts[1])
    raise ApiError(422, "invalid", "Invalid scope.")


def tree_response(user) -> dict:
    return {**tree.tree_payload(user), "saved": stories.saved_counts(user)}


def request_origin(request) -> str:
    return f"{request.scheme}://{request.get_host()}"


def learn_public_url(request) -> None:
    """R5.4: the public address is the origin of the most recent successful login, unless pinned."""
    if Setting.get("public_base_url_manual") == "1":
        return
    origin = request_origin(request)
    if Setting.get("public_base_url") != origin:
        Setting.put("public_base_url", origin)
        if PushSubscription.objects.filter(removing=False).exists():
            jobs.enqueue("websub_resubscribe_all")  # R4.11


def _set_timezone(user, data: dict) -> None:
    zone = data.get("timezone")
    if "timezone" not in (user.preferences or {}) and not prefs.invalid_keys({"timezone": zone}):
        user.preferences = {**(user.preferences or {}), "timezone": zone}
        user.save(update_fields=["preferences"])


def _wrap_tree_error(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tree.TreeError as e:
        raise ApiError(422, "invalid", str(e)) from None


# ------------------------------------------------------------------ session (item 22)


def _bootstrap(request, data):
    user = request.user
    if not user.is_authenticated:
        return error_response(401, "unauthenticated", "Log in to continue.", needs_setup=not User.objects.exists())
    return {
        "user": {"id": user.id, "username": user.username, "is_admin": user.is_superuser},
        "preferences": prefs.effective(user.preferences),
        "view_settings": view_settings_payload(user),
        "tree": tree_response(user),
        "worker": background.worker_state(),
        "version": settings.APP_VERSION,
    }


bootstrap = ensure_csrf_cookie(endpoint(auth=False, GET=_bootstrap))


def _client_address(request) -> str:
    if settings.TRUST_PROXY and request.headers.get("X-Forwarded-For"):
        return request.headers["X-Forwarded-For"].split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _throttled(keys: list[str]) -> bool:
    """R22.4: 10 attempts per 5 minutes per user name and per client address."""
    blocked = False
    for key in keys:
        count = cache.get(key, 0)
        if count >= 10:
            blocked = True
        cache.set(key, count + 1, 300)
    return blocked


def _login(request, data):
    username = require_str(data, "username", "", 150).strip()
    password = require_str(data, "password", "", 1000)
    if _throttled([f"login:user:{username.lower()}", f"login:addr:{_client_address(request)}"]):
        raise ApiError(429, "throttled", "Too many login attempts. Try again in a few minutes.")
    user = authenticate(request, username=username, password=password)
    if user is None:
        raise ApiError(401, "bad_credentials", "Wrong user name or password.")
    login(request, user)
    _set_timezone(user, data)
    learn_public_url(request)
    return {"ok": True}


def _logout(request, data):
    logout(request)
    return {"ok": True}


def _setup(request, data):
    """R22.2: the first user is created as superuser when the user table is empty."""
    username = require_str(data, "username", "", 150).strip()
    password = require_str(data, "password", "", 1000)
    if not username or len(password) < 6:
        raise ApiError(422, "invalid", "Choose a user name and a password of at least 6 characters.")
    with transaction.atomic():
        if User.objects.exists():
            raise ApiError(409, "already_set_up", "The application is already set up.")
        user = User.objects.create_superuser(username=username, password=password)
    login(request, user)
    _set_timezone(user, data)
    learn_public_url(request)
    return {"ok": True}


def _password(request, data):
    old = require_str(data, "old_password", "", 1000)
    new = require_str(data, "new_password", "", 1000)
    if not request.user.check_password(old):
        raise ApiError(422, "invalid", "The current password is wrong.")
    if len(new) < 6:
        raise ApiError(422, "invalid", "The new password needs at least 6 characters.")
    request.user.set_password(new)
    request.user.save()
    update_session_auth_hash(request, request.user)  # R22.3: other sessions are logged out
    return {"ok": True}


login_view = endpoint(auth=False, POST=_login)
logout_view = endpoint(auth=False, POST=_logout)
setup_view = endpoint(auth=False, POST=_setup)
password_view = endpoint(POST=_password)


def _instance_get(request, data):
    return {
        "public_base_url": Setting.get("public_base_url") or "",
        "manual": Setting.get("public_base_url_manual") == "1",
        "current_origin": request_origin(request),
    }


def _instance_put(request, data):
    value = data.get("public_base_url")
    before = Setting.get("public_base_url")
    if value is None or value == "":
        Setting.put("public_base_url_manual", "0")
        Setting.put("public_base_url", request_origin(request))
    else:
        parts = urlsplit(value) if isinstance(value, str) else None
        if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
            raise ApiError(422, "invalid", "Enter an address like https://reader.example.")
        Setting.put("public_base_url", f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}")
        Setting.put("public_base_url_manual", "1")
    if Setting.get("public_base_url") != before and PushSubscription.objects.filter(removing=False).exists():
        jobs.enqueue("websub_resubscribe_all")
    return _instance_get(request, data)


instance_view = endpoint(admin=True, GET=_instance_get, PUT=_instance_put)

# ------------------------------------------------------------------ tree (item 7)


def _tree(request, data):
    return tree_response(request.user)


def _sub_patch(request, data, feed_id):
    fields = {}
    if "title" in data:
        fields["user_title"] = require_str(data, "title", "", 255).strip() or None  # R7.9
    if "muted" in data:
        if not isinstance(data["muted"], bool):
            raise ApiError(422, "invalid", "'muted' must be true or false.")
        fields["muted"] = data["muted"]
    if not fields:
        raise ApiError(422, "invalid", "Nothing to change.")
    updated = tree.live_subs(request.user).filter(feed_id=feed_id).update(**fields)
    if not updated:
        raise ApiError(404, "not_found", "Subscription not found.")
    publish_tree(request)
    return {"ok": True}


def _sub_delete(request, data, feed_id):
    token = _wrap_tree_error(tree.delete_feed, request.user, feed_id)
    publish_tree(request)
    return {"undo_token": token}


def _folder_create(request, data):
    folder = _wrap_tree_error(tree.create_folder, request.user, require_str(data, "title", "", 255), data.get("parent_id"))
    publish_tree(request)
    return {"id": folder.id, "parent_id": folder.parent_id, "title": folder.title, "position": folder.position,
            "collapsed": folder.collapsed}  # fmt: skip


def _folder_patch(request, data, folder_id):
    folder = _wrap_tree_error(tree.get_folder, request.user, folder_id)
    fields = {}
    if "title" in data:
        title = require_str(data, "title", "", 255).strip()
        if not title:
            raise ApiError(422, "invalid", "A folder needs a name.")
        fields["title"] = title
    if "collapsed" in data:
        fields["collapsed"] = bool(data["collapsed"])
    if fields:
        type(folder).objects.filter(pk=folder.pk).update(**fields)
        publish_tree(request)
    return {"ok": True}


def _folder_delete(request, data, folder_id):
    token = _wrap_tree_error(tree.delete_folder, request.user, folder_id)
    publish_tree(request)
    return {"undo_token": token}


def _undo(request, data):
    if not tree.undo(request.user, require_str(data, "undo_token", "")):
        raise ApiError(410, "gone", "This can no longer be undone.")
    publish_tree(request)
    return tree_response(request.user)


def _move(request, data):
    _wrap_tree_error(
        tree.move, request.user, require_str(data, "item", ""), require_int(data, "parent_id"),
        require_int(data, "position", 0, minimum=0),
    )  # fmt: skip
    publish_tree(request)
    return {"ok": True}


tree_view = endpoint(GET=_tree)
subscription_view = endpoint(PATCH=_sub_patch, DELETE=_sub_delete)
folders_view = endpoint(POST=_folder_create)
folder_view = endpoint(PATCH=_folder_patch, DELETE=_folder_delete)
undo_view = endpoint(POST=_undo)
move_view = endpoint(POST=_move)

# ------------------------------------------------------------------ read state (item 6)


def _counts_for(user, feed_ids) -> dict:
    return {str(k): v for k, v in unread.unread_counts(user, feed_ids).items()}


def _read(request, data):
    ids = data.get("story_ids")
    if not isinstance(ids, list) or not all(type(i) is int for i in ids) or len(ids) > 5000:
        raise ApiError(422, "invalid", "'story_ids' must be a list of ids.")
    marked, feeds = unread.mark_stories_read(request.user, ids)
    counts = _counts_for(request.user, feeds)
    if marked:
        publish(request, "stories:read", {"ids": marked})
        publish(request, "counts", {"feeds": counts})
    return {"counts": counts}


def _unread(request, data):
    story_id = require_int(data, "story_id")
    try:
        feed_id = unread.mark_unread(request.user, story_id)
    except unread.TooOld as e:
        raise ApiError(409, "too_old", f"Story is more than {e.days} days old") from None
    if feed_id is None:
        raise ApiError(404, "not_found", "Story not found.")
    counts = _counts_for(request.user, [feed_id])
    publish(request, "stories:unread", {"ids": [story_id]})
    publish(request, "counts", {"feeds": counts})
    return {"counts": counts}


def _mark_read(request, data):
    user = request.user
    scope = require_str(data, "scope", "")
    parts = scope.split(":")
    subs = tree.live_subs(user)
    try:
        if parts[0] == "feed":
            subs = subs.filter(feed_id=int(parts[1]))
        elif parts[0] == "folder":
            subs = subs.filter(feed_id__in=tree.feeds_in_folder(user, int(parts[1])), muted=False)
        elif scope == "all":
            subs = subs.filter(muted=False)  # a muted feed is marked read only by itself
        else:
            raise ValueError
    except (ValueError, IndexError):
        raise ApiError(422, "invalid", "Invalid scope.") from None
    subs = list(subs)
    days = require_int(data, "days", minimum=0, maximum=365)
    cutoff = require_int(data, "cutoff", minimum=0)
    direction = data.get("direction")
    if direction not in (None, "older", "newer"):
        raise ApiError(422, "invalid", "Invalid direction.")
    if days:
        result = unread.mark_older_than_days(user, subs, days)
    else:
        result = unread.mark_feeds_read(user, subs, cutoff, direction)
    for feed_id, date in result["moved"].items():
        if date is not None:
            publish(request, "feed:read", {"feed_id": feed_id, "mark_read_date": date})
    if result["story_ids"]:
        publish(request, "stories:read", {"ids": result["story_ids"]})
    counts = _counts_for(user, [s.feed_id for s in subs])
    publish(request, "counts", {"feeds": counts})
    response = {"counts": counts}
    # A whole feed or folder marked read only moves read dates, so it can be undone (`subs` still
    # hold the dates from before).
    if parts[0] in ("feed", "folder") and not days and cutoff is None:
        response["undo_token"] = unread.mark_read_undo_token(user, subs, result["moved"])
    return response


def _mark_read_undo(request, data):
    restored = unread.undo_mark_read(request.user, require_str(data, "undo_token", ""))
    if restored is None:
        raise ApiError(410, "gone", "This can no longer be undone.")
    feed_ids, story_ids = restored
    counts = _counts_for(request.user, feed_ids)
    if story_ids:
        publish(request, "stories:unread", {"ids": story_ids})
    publish(request, "counts", {"feeds": counts})
    return {"counts": counts, "story_ids": story_ids}


def _counts(request, data):
    feed_id = request.GET.get("feed_id")
    feed_ids = [int(feed_id)] if feed_id and feed_id.isdigit() else None
    return {"feeds": _counts_for(request.user, feed_ids), "saved": stories.saved_counts(request.user)}


read_view = endpoint(POST=_read)
unread_view = endpoint(POST=_unread)
mark_read_view = endpoint(POST=_mark_read)
mark_read_undo_view = endpoint(POST=_mark_read_undo)
counts_view = endpoint(GET=_counts)

# ------------------------------------------------------------------ stories (items 10, 12, 17)


def _stories(request, data):
    q = request.GET
    try:
        return stories.list_stories(
            request.user, q.get("scope", ""), q.get("filter", "all"), q.get("order", "newest"), q.get("q", ""),
            q.get("cursor"), int(q.get("limit") or 30), q.get("content", "1") != "0",
        )  # fmt: skip
    except stories.ScopeError as e:
        raise ApiError(404, "not_found", str(e)) from None
    except ValueError:
        raise ApiError(422, "invalid", "Invalid parameters.") from None


def _story(request, data, story_id):
    story = stories.get_story(request.user, story_id)
    if story is None:
        raise ApiError(404, "not_found", "Story not found.")
    return story


def _saved_event(request, story_id, saved_id, saved: bool, user_tags) -> dict:
    counts = stories.saved_counts(request.user)
    payload = {"story_id": story_id, "saved_id": saved_id, "saved": saved, "user_tags": user_tags, "counts": counts}
    publish(request, "saved", payload)
    return payload


def _save(request, data):
    saved = stories.save_story(request.user, require_int(data, "story_id"), data.get("user_tags"))
    if saved is None:
        raise ApiError(404, "not_found", "Story not found.")
    return _saved_event(request, stories.live_story_id(saved), saved.id, True, saved.user_tags)


def _saved_patch(request, data, saved_id):
    saved = stories.set_saved_tags(request.user, saved_id, data.get("user_tags"))
    if saved is None:
        raise ApiError(404, "not_found", "Saved story not found.")
    return _saved_event(request, stories.live_story_id(saved), saved.id, True, saved.user_tags)


def _saved_delete(request, data, saved_id):
    saved = stories.unsave(request.user, saved_id)
    if saved is None:
        raise ApiError(404, "not_found", "Saved story not found.")
    return _saved_event(request, stories.live_story_id(saved), saved_id, False, [])


stories_view = endpoint(GET=_stories)
story_view = endpoint(GET=_story)
saved_view = endpoint(POST=_save)
saved_item_view = endpoint(PATCH=_saved_patch, DELETE=_saved_delete)

# ------------------------------------------------------------------ settings (item 19)


def _publish_settings(request) -> None:
    publish(
        request, "preferences",
        {"preferences": prefs.effective(request.user.preferences), "view_settings_changed": True,
         "view_settings": view_settings_payload(request.user)},
    )  # fmt: skip


def _view_put(request, data):
    target_type, target_id = scope_target(require_str(data, "scope", ""))
    key, value = data.get("key"), data.get("value")
    if key not in prefs.VIEW_SETTING_KEYS or value not in prefs.VIEW_SETTING_KEYS[key]:
        raise ApiError(422, "invalid", "Invalid view setting.")
    ViewSetting.objects.update_or_create(
        user=request.user, target_type=target_type, target_id=target_id, key=key, defaults={"value": value}
    )
    _publish_settings(request)
    return {"view_settings": view_settings_payload(request.user)}


def _view_delete(request, data):
    key = data.get("key") or request.GET.get("key")
    if key not in prefs.VIEW_SETTING_KEYS:
        raise ApiError(422, "invalid", "Invalid view setting.")
    rows = ViewSetting.objects.filter(user=request.user)
    scope = data.get("scope")
    if scope:
        target_type, target_id = scope_target(scope)
        rows.filter(target_type=target_type, target_id=target_id, key=key).delete()
    else:
        # R19.4: reset for all scopes; resetting the order also resets the read filter.
        rows.filter(key__in=["order", "read_filter"] if key == "order" else [key]).delete()
    _publish_settings(request)
    return {"view_settings": view_settings_payload(request.user)}


def _preferences(request, data):
    bad = prefs.invalid_keys(data)
    if bad:
        raise ApiError(422, "invalid", "Invalid preference: " + ", ".join(sorted(bad)))  # R19.8
    user = request.user
    user.preferences = {**(user.preferences or {}), **data}
    user.save(update_fields=["preferences"])
    _publish_settings(request)
    return {"preferences": prefs.effective(user.preferences)}


view_settings_view = endpoint(PUT=_view_put, DELETE=_view_delete)
preferences_view = endpoint(PATCH=_preferences)

# ------------------------------------------------------------------ OPML (item 15)


def _opml_import(request, data):
    upload = request.FILES.get("file")
    if upload is None:
        raise ApiError(422, "invalid", "Choose an OPML file.")
    if upload.size > 5 * 1024 * 1024:
        raise ApiError(413, "too_large", "The file is larger than 5 MB.")
    try:
        result = opml.import_opml(request.user, upload.read())
    except opml.OpmlError as e:
        raise ApiError(422, "invalid", str(e)) from None
    if result["feeds_added"]:
        jobs.enqueue("schedule_now")
    publish_tree(request)
    return result


def _opml_export(request, data):
    name = f"feeds-{request.user.username}-{datetime.now(timezone.utc):%Y-%m-%d}.opml"
    response = HttpResponse(opml.export_opml(request.user), content_type="text/x-opml")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


opml_view = endpoint(GET=_opml_export, POST=_opml_import)

# ------------------------------------------------------------------ feeds: refresh, icon, statistics


def _require_subscription(user, feed_id: int) -> Subscription:
    sub = tree.live_subs(user).filter(feed_id=feed_id).select_related("feed").first()
    if sub is None:
        raise ApiError(404, "not_found", "Feed not found.")
    return sub


def _refresh(request, data, feed_id):
    _require_subscription(request.user, feed_id)
    job, _ = jobs.request_fetch(feed_id, forced=True)
    response = endpoint_json({"state": job.state}, 202)
    return response


def endpoint_json(data: dict, status: int):
    from django.http import JsonResponse

    return JsonResponse(data, status=status)


def _icon(request, data, feed_id):
    icon = FeedIcon.objects.filter(feed_id=feed_id).first()
    if icon is None:
        raise ApiError(404, "not_found", "No icon.")
    etag = f'"{feed_id}-{icon.updated_at}"'
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
    else:
        response = HttpResponse(bytes(icon.data), content_type=icon.content_type)
    response["ETag"] = etag
    response["Cache-Control"] = "private, max-age=86400"
    return response


def _month_series(counts: dict[str, int]) -> list:
    if not counts:
        return []
    first = min(counts)
    year, month = int(first[:4]), int(first[5:7])
    today = datetime.now(timezone.utc)
    series = []
    while (year, month) <= (today.year, today.month):
        key = f"{year:04d}-{month:02d}"
        series.append([key, counts.get(key, 0)])
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return series


def _log_rows(feed_id: int, log_type: str) -> list:
    rows = FeedFetchLog.objects.filter(feed_id=feed_id, type=log_type).order_by("-id")[:25]
    return [{"at": r.at, "code": r.status_code, "message": r.message, "duration_ms": r.duration_ms} for r in rows]


def _statistics(request, data, feed_id):
    """R18.5: reads stored values only."""
    sub = _require_subscription(request.user, feed_id)
    feed = sub.feed
    counts: dict[str, dict[str, int]] = {"month": {}, "weekday": {}, "hour": {}}
    for period_type, key, count in FeedStoryCount.objects.filter(feed_id=feed_id).values_list("period_type", "period_key", "count"):
        counts.setdefault(period_type, {})[key] = count
    zone = prefs.effective(request.user.preferences)["timezone"]
    try:
        offset = round(datetime.now(ZoneInfo(zone)).utcoffset().total_seconds() / 3600)
    except Exception:
        offset = 0
    hours = [0] * 24
    for key, count in counts["hour"].items():
        hours[(int(key) + offset) % 24] = count  # R18.2
    push = PushSubscription.objects.filter(feed_id=feed_id, removing=False).first()
    reason = ""
    if feed.errors_since_good > 0:
        reason = f"{feed.errors_since_good} failed fetch{'es' if feed.errors_since_good != 1 else ''} in a row"
    elif push and push.verified and push.last_push_at:
        reason = f"real-time updates through {urlsplit(push.hub).hostname or push.hub}"
    elif feed.stories_last_month < 30 and feed.min_to_decay > 45:
        reason = "few stories lately"
    size = Story.objects.filter(feed_id=feed_id).aggregate(n=Sum(Length("content")))["n"] or 0
    return {
        "feed": {"id": feed.id, "title": sub.user_title or feed.title, "feed_address": feed.feed_address,
                 "feed_link": feed.feed_link, "has_exception": feed.has_exception,
                 "exception_code": feed.exception_code},
        "last_update": feed.last_update,
        "next_update": feed.next_scheduled_update,
        "interval_minutes": feed.min_to_decay,
        "reason": reason,
        "push": {"hub": push.hub, "verified": push.verified, "lease_expires": push.lease_expires,
                 "last_push_at": push.last_push_at} if push else None,
        "stories_last_month": feed.stories_last_month,
        "average_stories_per_month": feed.average_stories_per_month,
        "months": _month_series(counts["month"]),
        "weekdays": [counts["weekday"].get(str(day), 0) for day in range(7)],
        "hours": hours,
        "fetch_history": _log_rows(feed_id, "feed"),
        "push_history": _log_rows(feed_id, "push"),
        "story_count": Story.objects.filter(feed_id=feed_id).count(),
        "content_bytes": size,
        "last_load_ms": feed.last_load_ms,
    }  # fmt: skip


def _settings_get(request, data, feed_id):
    """What the feed settings dialog needs besides the tree's feed entry."""
    _require_subscription(request.user, feed_id)
    return {"proxy_available": bool(settings.PROXY_URL), "fetch_history": _log_rows(feed_id, "feed")}


def _settings_patch(request, data, feed_id):
    """PATCH {use_proxy}: a setting of the feed itself, shared by all its subscribers, who are all told.
    Changing it fetches the feed again."""
    sub = _require_subscription(request.user, feed_id)
    use_proxy = data.get("use_proxy")
    if type(use_proxy) is not bool:
        raise ApiError(422, "invalid", "'use_proxy' must be true or false.")
    if use_proxy != sub.feed.use_proxy:
        with transaction.atomic():
            Feed.objects.filter(pk=feed_id).update(use_proxy=use_proxy)
        jobs.request_fetch(feed_id, forced=True)
        subscribers = Subscription.objects.filter(feed_id=feed_id, deleted_at__isnull=True).values_list("user_id", flat=True)
        for user_id in set(subscribers):
            origin = client_id(request) if user_id == request.user.id else None
            bus.publish(user_id, "feed:settings", {"feed_id": feed_id, "use_proxy": use_proxy}, origin=origin)
    return {"use_proxy": use_proxy}


refresh_view = endpoint(POST=_refresh)
feed_settings_view = endpoint(GET=_settings_get, PATCH=_settings_patch)
icon_view = endpoint(GET=_icon)
statistics_view = endpoint(GET=_statistics)

# ------------------------------------------------------------------ the application shell (R20.8)


@ensure_csrf_cookie
def index(request, **kwargs):
    """Any non-API path returns the built frontend; the client router takes over."""
    path = Path(settings.FRONTEND_DIST) / "index.html"
    if not path.exists():
        return HttpResponse(
            "<h1>NewsSharp</h1><p>The frontend is not built. Run <code>npm install</code> and "
            "<code>npm run build</code> in <code>frontend/</code>.</p>",
            status=503,
        )
    response = HttpResponse(path.read_bytes(), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response


def api_not_found(request, **kwargs):
    return error_response(404, "not_found", "Unknown API route.")


def recent_fetch_job(feed_id: int) -> Job | None:
    return Job.objects.filter(type="fetch_feed", feed_id=feed_id).order_by("-id").first()
