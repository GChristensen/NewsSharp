"""Fetch interval computation (worker/scheduling.py)."""
import random
import re
from email.utils import parsedate_to_datetime

from django.conf import settings

MONTH = 30 * 86400
MAX_ERROR_MINUTES = 7 * 24 * 60
MAX_PUSH_MINUTES = 24 * 60
MAX_HINT_SECONDS = 24 * 3600


def base_interval_minutes(stories_last_month: int, last_story_date: int, now_ts: int) -> float:
    """The interval of a healthy feed without push."""
    spd = stories_last_month / 30
    if spd >= 1:
        minutes = 45
    elif spd > 0:
        minutes = 360 - spd * 360
    else:
        months = max(1, int((now_ts - last_story_date) / MONTH)) if last_story_date else 1
        minutes = 720 * months
    return min(minutes, settings.FETCH_INTERVAL_MINUTES)


def interval_minutes(
    stories_last_month: int, last_story_date: int, errors: int, push_active: bool, now_ts: int,
    hint_seconds: int | None = None,
) -> int:  # fmt: skip
    """The fetch interval in minutes. A server hint only lengthens the interval: it never makes a quiet feed
    polled more often or cancels the error backoff."""
    minutes = base_interval_minutes(stories_last_month, last_story_date, now_ts)
    if push_active:
        minutes = min(minutes * 12, MAX_PUSH_MINUTES)
    if errors > 0:
        minutes = min(minutes * errors, MAX_ERROR_MINUTES)
    if hint_seconds and hint_seconds > 0:
        minutes = max(minutes, min(hint_seconds, MAX_HINT_SECONDS) / 60)
    return max(1, round(minutes))


def next_update(minutes: int, now_ts: int) -> int:
    """next = now + interval + random(0, interval) / 4."""
    seconds = minutes * 60
    return now_ts + seconds + int(random.uniform(0, seconds) / 4)


def server_hint_seconds(headers: dict, now_ts: int) -> int | None:
    """The server hint: the longer of Cache-Control max-age and Retry-After."""
    values = []
    match = re.search(r"max-age\s*=\s*(\d+)", headers.get("cache-control", ""), re.I)
    if match:
        values.append(int(match.group(1)))
    retry = (headers.get("retry-after") or "").strip()
    if retry.isdigit():
        values.append(int(retry))
    elif retry:
        try:
            values.append(int(parsedate_to_datetime(retry).timestamp()) - now_ts)
        except (TypeError, ValueError):
            pass
    values = [v for v in values if v > 0]
    return max(values) if values else None
