"""`reader import_newsblur export.json` (worker/management/commands/import_newsblur.py):
reads the file written by NewsBlurMod's utils/export_for_reader.py into an empty database.
Stories, read state, saved stories and passwords are not migrated."""
import base64
import json
import random
import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts import prefs
from accounts.models import User
from feeds.models import Feed, FeedIcon
from feeds.util import InvalidUrl, normalize_url, now
from reading.models import Folder, Subscription, ViewSetting

VIEW_KEYS = {"v": "view", "l": "layout", "o": "order", "r": "read_filter"}


def convert_preferences(profile: dict) -> dict:
    """Only catalog keys with valid values are taken."""
    candidates = dict(profile.get("preferences") or {})
    for key in ("timezone", "days_of_unread", "feed_pane_size"):
        if profile.get(key) is not None:
            candidates[key] = profile[key]
    result = {}
    for key, value in candidates.items():
        if key in prefs.CATALOG and not prefs.invalid_keys({key: value}):
            result[key] = value
    return result


class Command(BaseCommand):
    help = "Import users, feeds, subscriptions, folders, icons and settings exported from NewsBlurMod."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--map", default="migration_map.json", help="where to write old feed id -> new feed id")

    def handle(self, *args, **options):
        with open(options["file"], encoding="utf-8") as handle:
            data = json.load(handle)
        if User.objects.exists() or Feed.objects.exists():
            raise CommandError("The database is not empty; import_newsblur only runs on an empty database.")
        with transaction.atomic():
            feed_map, passwords = self.run_import(data)
        with open(options["map"], "w", encoding="utf-8") as handle:
            json.dump({str(old): new for old, new in feed_map.items()}, handle, indent=1)
        self.stdout.write(f"feed id map written to {options['map']}")
        self.stdout.write("One-time passwords (change them after the first login):")
        for username, password in passwords:
            self.stdout.write(f"  {username}: {password}")

    def run_import(self, data: dict):
        ts = now()
        users, passwords = {}, []
        for row in data["users"]:
            password = secrets.token_urlsafe(9)
            profile = (data.get("preferences") or {}).get(str(row["id"]), {})
            user = User(
                username=row["username"], is_staff=bool(row.get("is_staff")), is_superuser=bool(row.get("is_staff")),
                preferences=convert_preferences(profile),
            )  # fmt: skip
            user.set_password(password)
            user.save()
            users[row["id"]] = user
            passwords.append((user.username, password))

        feed_map: dict[int, int] = {}
        by_address: dict[str, Feed] = {}
        skipped = 0
        for row in data["feeds"]:
            try:
                address = normalize_url(row["feed_address"])
            except InvalidUrl:
                skipped += 1
                continue
            feed = by_address.get(address)
            if feed is None:
                feed = Feed.objects.create(
                    feed_address=address, feed_link=(row.get("feed_link") or "")[:1000],
                    title=(row.get("feed_title") or "")[:255], favicon_color=(row.get("favicon_color") or "")[:6],
                    favicon_not_found=bool(row.get("favicon_not_found")), created_at=ts,
                    next_scheduled_update=ts + random.randint(0, 300),  # spread over the first 5 minutes
                )  # fmt: skip
                by_address[address] = feed
            feed_map[row["id"]] = feed.id

        for row in data.get("icons") or []:
            feed_id = feed_map.get(row["feed_id"])
            if feed_id is None or FeedIcon.objects.filter(feed_id=feed_id).exists():
                continue
            try:
                image = base64.b64decode(row["data"])
            except (ValueError, TypeError):
                continue
            FeedIcon.objects.create(feed_id=feed_id, data=image, icon_url=(row.get("icon_url") or "")[:1000], updated_at=ts)
            Feed.objects.filter(pk=feed_id).update(
                favicon_color=(row.get("color") or "")[:6], favicon_not_found=False, favicon_checked_at=ts
            )

        titles: dict[tuple[int, int], str | None] = {}
        for row in data["subscriptions"]:
            if row["user_id"] in users and row["feed_id"] in feed_map:
                titles[(row["user_id"], feed_map[row["feed_id"]])] = row.get("user_title") or None

        for old_user_id, user in users.items():
            profile = (data.get("preferences") or {}).get(str(old_user_id), {})
            collapsed = set(profile.get("collapsed_folders") or [])
            placed: set[int] = set()
            first_folder: dict[str, int] = {}

            def subscribe(feed_id: int, folder_id, position: int, _user=user, _old=old_user_id, _placed=placed):
                Subscription.objects.create(
                    user=_user, feed_id=feed_id, folder_id=folder_id, position=position,
                    user_title=titles[(_old, feed_id)], mark_read_date=ts - 86400, created_at=ts,
                )  # fmt: skip
                _placed.add(feed_id)

            def walk(items, parent_id, _user=user, _old=old_user_id, _placed=placed, _first=first_folder, _collapsed=collapsed):
                """An integer is a feed, an object is a folder; positions are list indexes."""
                position = 0
                for item in items if isinstance(items, list) else []:
                    if isinstance(item, int):
                        feed_id = feed_map.get(item)
                        if feed_id is None or (_old, feed_id) not in titles or feed_id in _placed:
                            continue
                        subscribe(feed_id, parent_id, position)
                        position += 1
                    elif isinstance(item, dict):
                        for title, children in item.items():
                            folder = Folder.objects.create(
                                user=_user, parent_id=parent_id, title=str(title)[:255] or "Folder", position=position,
                                collapsed=title in _collapsed,
                            )  # fmt: skip
                            _first.setdefault(str(title), folder.id)
                            position += 1
                            walk(children, folder.id)
                return position

            next_root = walk((data.get("folders") or {}).get(str(old_user_id), []), None)
            for (owner, feed_id) in titles:  # subscriptions missing from the tree go to the root
                if owner == old_user_id and feed_id not in placed:
                    subscribe(feed_id, None, next_root)
                    next_root += 1
            self.import_view_settings(user, (data.get("view_settings") or {}).get(str(old_user_id), {}), feed_map, first_folder)

        self.stdout.write(
            f"imported {len(users)} users, {len(by_address)} feeds ({skipped} skipped), {len(titles)} subscriptions"
        )
        return feed_map, passwords

    def import_view_settings(self, user, settings: dict, feed_map: dict, folders: dict) -> None:
        """Map NewsBlur's view settings keys to scopes: feed ids, river:<folder>, river: and starred."""
        for key, value in settings.items():
            if str(key).isdigit():
                target = ("feed", feed_map.get(int(key)))
            elif key == "river:":
                target = ("all", 0)
            elif str(key).startswith("river:"):
                target = ("folder", folders.get(str(key)[6:]))
            elif key == "starred":
                target = ("saved", 0)
            else:
                continue
            if target[1] is None:
                continue
            if isinstance(value, str):
                value = {"v": value}  # a legacy string value is the view
            if not isinstance(value, dict):
                continue
            for short, name in VIEW_KEYS.items():
                item = value.get(short)
                if item == "page":
                    item = "original"
                if item in prefs.VIEW_SETTING_KEYS[name]:
                    ViewSetting.objects.update_or_create(
                        user=user, target_type=target[0], target_id=target[1], key=name, defaults={"value": item}
                    )
