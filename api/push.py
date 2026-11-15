"""The WebSub callback routes (api/push.py), R4.5 and R4.6: the web process's only part in WebSub.
Both are unauthenticated, outside /api and exempt from CSRF (they are authenticated by their token)."""
import hashlib
import hmac
import logging

from django.db import transaction
from django.http import HttpResponse, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt

from feeds import store
from feeds.models import Feed, PushSubscription
from feeds.util import now
from worker.models import WorkerEvent

from . import jobs

log = logging.getLogger("web.push")

MAX_BODY = 10 * 1024 * 1024
ALGORITHMS = {"sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha384": hashlib.sha384, "sha512": hashlib.sha512}


def valid_signature(header: str, secret: str, body: bytes) -> bool:
    """X-Hub-Signature: <method>=<hex>, an HMAC of the raw body; compared in constant time."""
    method, _, digest = (header or "").partition("=")
    algorithm = ALGORITHMS.get(method.strip().lower())
    if algorithm is None or not digest:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, algorithm).hexdigest()
    return hmac.compare_digest(expected, digest.strip().lower())


def _verify(request, push: PushSubscription):
    mode = request.GET.get("hub.mode", "")
    topic = request.GET.get("hub.topic", "")
    challenge = request.GET.get("hub.challenge", "")
    if mode == "denied":
        with transaction.atomic():
            if push.feed_id:
                Feed.objects.filter(pk=push.feed_id).update(is_push=False)
                WorkerEvent.objects.create(at=now(), type="push_state", feed_id=push.feed_id, payload={"is_push": False})
            push.delete()
        return HttpResponse("", content_type="text/plain")
    if topic != push.topic or not challenge:
        return HttpResponseNotFound("unknown subscription")
    if mode == "unsubscribe":
        if not push.removing:
            return HttpResponseNotFound("not unsubscribing")  # confirmed only while being removed
        push.delete()
        return HttpResponse(challenge, content_type="text/plain")
    if mode != "subscribe" or push.removing:
        return HttpResponseNotFound("unknown subscription")
    ts = now()
    try:
        lease = int(request.GET.get("hub.lease_seconds") or 0)
    except ValueError:
        lease = 0
    with transaction.atomic():
        PushSubscription.objects.filter(pk=push.pk).update(
            verified=True, lease_seconds=lease, lease_expires=ts + lease if lease else 0
        )
        if push.feed_id:
            Feed.objects.filter(pk=push.feed_id).update(is_push=True)
            WorkerEvent.objects.create(at=ts, type="push_state", feed_id=push.feed_id, payload={"is_push": True})
    log.info("push subscription %s verified for feed %s (lease %ss)", push.id, push.feed_id, lease)
    return HttpResponse(challenge, content_type="text/plain")


@csrf_exempt
def callback(request, push_id: int, token: str):
    push = PushSubscription.objects.filter(pk=push_id).first()
    if push is None or not hmac.compare_digest(push.callback_token, token):
        return HttpResponseNotFound("unknown subscription")
    if request.method == "GET":
        return _verify(request, push)
    if request.method != "POST":
        return HttpResponse(status=405)
    if push.removing or not push.feed_id:
        return HttpResponseNotFound("unknown subscription")
    body = request.body
    if len(body) > MAX_BODY:
        return HttpResponse(status=413)
    signature = request.headers.get("X-Hub-Signature") or request.headers.get("X-Hub-Signature-256") or ""
    if not valid_signature(signature, push.secret, body):
        # R4.6: answered 202 and discarded, as the protocol prescribes.
        store.log_fetch(push.feed_id, "push", 0, "Bad signature", 0)
        log.info("push for feed %s discarded: bad signature", push.feed_id)
        return HttpResponse(status=202)
    jobs.enqueue(
        "push_body", feed_id=push.feed_id, payload={"content_type": request.content_type or ""}, blob=body,
        interactive=False,
    )  # fmt: skip
    log.info("push for feed %s accepted (%d bytes)", push.feed_id, len(body))
    return HttpResponse(status=202)
