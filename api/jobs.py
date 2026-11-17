"""Jobs requested by the web process (api/jobs.py), R0.9-R0.12."""
import asyncio

from asgiref.sync import sync_to_async
from django.db import connection, transaction

from feeds.util import now
from worker import wakeup
from worker.models import INTERACTIVE_TYPES, Job

from . import background
from .http import ApiError

WAIT_SECONDS = 30  # R0.12


def enqueue(job_type: str, feed_id=None, user_id=None, payload=None, blob=None, interactive=None) -> Job:
    payload = payload or {}
    if interactive is None:
        interactive = (
            job_type in INTERACTIVE_TYPES
            or (job_type == "fetch_feed" and bool(payload.get("forced")))
            or (job_type == "fetch_icon" and bool(payload.get("force")))
        )
    job = Job.objects.create(
        type=job_type, feed_id=feed_id, user_id=user_id, payload=payload, blob=blob, interactive=interactive,
        created_at=now(),
    )  # fmt: skip
    wakeup.notify()
    return job


def request_fetch(feed_id: int, forced: bool = False) -> tuple[Job, bool]:
    """R0.11, R3.9: at most one fetch_feed job per feed is queued or running, and at most one
    forced fetch per 10 s. Returns (job, created)."""
    with transaction.atomic():
        active = Job.objects.filter(type="fetch_feed", feed_id=feed_id, state__in=("queued", "running")).first()
        if active is not None:
            return active, False
        if forced:
            recent = (
                Job.objects.filter(type="fetch_feed", feed_id=feed_id, finished_at__gt=now() - 10, payload__forced=True)
                .order_by("-id")
                .first()
            )
            if recent is not None:
                return recent, False
        return enqueue("fetch_feed", feed_id=feed_id, payload={"forced": forced}), True


def _create(job_type, feed_id, user_id, payload) -> int:
    try:
        return enqueue(job_type, feed_id=feed_id, user_id=user_id, payload=payload).id
    finally:
        connection.close()


def _state(job_id: int):
    try:
        return Job.objects.filter(pk=job_id).values("state", "result", "error").first()
    finally:
        connection.close()


async def run_and_wait(job_type: str, feed_id=None, user_id=None, payload=None) -> dict:
    """Queue a job and wait for its result, up to 30 s; then 504 worker_timeout (R0.12)."""
    job_id = await sync_to_async(_create, thread_sensitive=False)(job_type, feed_id, user_id, payload)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_SECONDS
    while loop.time() < deadline:
        await background.wait_tick(0.2)
        job = await sync_to_async(_state, thread_sensitive=False)(job_id)
        if job is None:
            break
        if job["state"] == "done":
            return job["result"] or {}
        if job["state"] == "failed":
            raise ApiError(422, "job_failed", job["error"] or "The request could not be completed.")
    raise ApiError(504, "worker_timeout", "The worker did not answer in time. Feeds may not be updating.")
