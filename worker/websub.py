"""WebSub hub requests of the worker (worker/websub.py), R4.1-R4.4, R4.9-R4.11. The callback routes
live in the web process (api/push.py)."""
import logging
import secrets

import httpx
from django.conf import settings
from django.db import transaction

from feeds import store
from feeds.models import Feed, PushSubscription, Setting
from feeds.util import now
from worker.models import WorkerEvent

log = logging.getLogger("worker.websub")

WEEK = 7 * 86400
RENEWAL_RETRY_SECONDS = 3600


def detach(push: PushSubscription, ts: int) -> dict:
    """Turn a subscription into a pending unsubscribe (R4.10). Runs in the caller's transaction."""
    feed_id = push.feed_id
    info = {"id": push.id, "hub": push.hub, "topic": push.topic, "token": push.callback_token,
            "secret": push.secret, "base": push.callback_base, "feed_id": None}  # fmt: skip
    PushSubscription.objects.filter(pk=push.pk).update(removing=True, feed=None, verified=False, requested_at=ts)
    if feed_id:
        Feed.objects.filter(pk=feed_id).update(is_push=False)
        WorkerEvent.objects.create(at=ts, type="push_state", feed_id=feed_id, payload={"is_push": False})
    return info


def lease_nearly_over(push: PushSubscription, ts: int) -> bool:
    """R4.9: less than a third of the lease, or 7 days, whichever is shorter."""
    if not push.lease_expires:
        return False
    return push.lease_expires - ts < min(push.lease_seconds / 3 if push.lease_seconds else WEEK, WEEK)


def plan(feed_id: int, hub: str | None, topic: str | None, force: bool) -> dict:
    """Decide what to ask the hub for a feed and prepare the subscription row."""
    ts = now()
    with transaction.atomic():
        push = PushSubscription.objects.filter(feed_id=feed_id, removing=False).first()
        if not hub:
            if push is not None:
                return {"action": "unsubscribe", **detach(push, ts)}
            return {"action": None}
        base = Setting.get("public_base_url")
        if not base:
            return {"action": None}  # R4.4: postponed until the public address is known
        topic = topic or ""
        if push is not None and push.hub == hub and push.topic == topic and push.callback_base == base and not force:
            if push.verified and not lease_nearly_over(push, ts):
                return {"action": None}
            if not push.verified and ts - push.requested_at < 300:
                return {"action": None}
        if push is None:
            push = PushSubscription(
                feed_id=feed_id, callback_token=secrets.token_urlsafe(24), secret=secrets.token_hex(32)
            )
        elif push.hub != hub or push.topic != topic:
            push.verified = False
        push.hub, push.topic, push.callback_base, push.requested_at = hub[:1000], topic[:1000], base, ts
        push.save()
        return {"action": "subscribe", "id": push.id, "hub": push.hub, "topic": push.topic,
                "token": push.callback_token, "secret": push.secret, "base": base, "feed_id": feed_id}  # fmt: skip


def renewals_due() -> list[dict]:
    """R4.9. A renewal is asked for at most once per RENEWAL_RETRY_SECONDS: the hub confirms it
    later through the callback, and until then the lease still looks nearly over."""
    ts = now()
    due = []
    pushes = PushSubscription.objects.filter(
        verified=True, removing=False, feed__isnull=False, requested_at__lte=ts - RENEWAL_RETRY_SECONDS
    )
    for push in pushes:
        if lease_nearly_over(push, ts):
            due.append({"feed_id": push.feed_id, "hub": push.hub, "topic": push.topic})
    return due


def expire_leases() -> None:
    """A lease that ran out without renewal no longer delivers: the feed is polled as an ordinary
    feed again, and the next successful fetch subscribes anew (plan(), at most every 300 s)."""
    ts = now()
    expired = list(
        PushSubscription.objects.filter(
            verified=True, removing=False, feed__isnull=False, lease_expires__gt=0, lease_expires__lt=ts
        )
    )
    if not expired:
        return
    with transaction.atomic():
        for push in expired:
            PushSubscription.objects.filter(pk=push.pk).update(verified=False)
            Feed.objects.filter(pk=push.feed_id).update(is_push=False)
            WorkerEvent.objects.create(at=ts, type="push_state", feed_id=push.feed_id, payload={"is_push": False})
            log.info("websub lease of feed %s expired", push.feed_id)


def all_subscriptions() -> list[dict]:
    return list(PushSubscription.objects.filter(removing=False, feed__isnull=False).values("feed_id", "hub", "topic"))


def _log_failure(feed_id: int | None, message: str) -> None:
    if feed_id and Feed.objects.filter(pk=feed_id).exists():
        store.log_fetch(feed_id, "push", 0, message, 0)


async def send(coordinator, request: dict) -> None:
    """POST a subscribe or unsubscribe request to the hub (R4.3)."""
    mode = request["action"]
    data = {
        "hub.mode": mode,
        "hub.topic": request["topic"],
        "hub.callback": f"{request['base'].rstrip('/')}/push/{request['id']}/{request['token']}",
        "hub.verify": ["async", "sync"],
        "hub.verify_token": request["token"],
    }
    if mode == "subscribe":
        data["hub.lease_seconds"] = str(settings.PUSH_LEASE_SECONDS)
        data["hub.secret"] = request["secret"]
    try:
        response = await coordinator.client.post(request["hub"], data=data, timeout=20, follow_redirects=True)
        status = response.status_code
    except (httpx.HTTPError, OSError, ValueError) as e:
        status = 0
        log.info("websub %s feed=%s hub=%s failed: %s", mode, request["feed_id"], request["hub"], e)
    if status in (202, 204):
        log.info("websub %s feed=%s hub=%s -> %s", mode, request["feed_id"], request["hub"], status)
    elif mode == "subscribe":
        await coordinator.db(_log_failure, request["feed_id"], f"Hub subscription failed ({status or 'no response'})")


async def check(coordinator, feed_id: int, hub: str | None, topic: str | None, force: bool = False) -> None:
    """Subscribe, renew or unsubscribe a feed according to its hub link (R4.2, R4.10)."""
    request = await coordinator.db(plan, feed_id, hub, topic, force)
    if request["action"]:
        await send(coordinator, request)
