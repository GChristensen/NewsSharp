"""OPML import and export (reading/opml.py). Import makes no network request."""
from datetime import datetime, timezone

from django.db import transaction
from lxml import etree

from feeds.models import Feed
from feeds.util import InvalidUrl, normalize_url, now

from . import tree
from .models import Folder, Subscription


class OpmlError(Exception):
    pass


def import_opml(user, data: bytes) -> dict:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=True, huge_tree=False)
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as e:
        raise OpmlError("The file is not valid OPML.") from e
    body = root.find("body") if root is not None else None
    if body is None:
        raise OpmlError("The file is not valid OPML.")
    result = {"feeds_added": 0, "feeds_existing": 0, "folders_added": 0, "skipped": []}
    ts = now()
    seen: set[str] = set()
    existing_feeds = {f.feed_address: f for f in Feed.objects.all()}
    subscribed = set(Subscription.objects.filter(user=user, deleted_at__isnull=True).values_list("feed_id", flat=True))

    def walk(element, parent_id):
        siblings = {f.title: f for f in tree.live_folders(user).filter(parent_id=parent_id)}
        position = tree.next_position(user, parent_id)
        for outline in element.findall("outline"):
            title = (outline.get("title") or outline.get("text") or "").strip()
            xml_url = outline.get("xmlUrl")
            if xml_url is None:
                name = (outline.get("text") or outline.get("title") or "").strip()[:255]
                if not name:
                    continue
                folder = siblings.get(name)  # matched to an existing sibling with the same title
                if folder is None:
                    folder = Folder.objects.create(user=user, parent_id=parent_id, title=name, position=position)
                    siblings[name] = folder
                    position += 1
                    result["folders_added"] += 1
                walk(outline, folder.id)
                continue
            try:
                address = normalize_url(xml_url)
            except InvalidUrl as e:
                result["skipped"].append({"title": title, "url": xml_url, "reason": str(e)})
                continue
            if address in seen:
                continue  # a feed listed several times is placed at its first occurrence
            seen.add(address)
            feed = existing_feeds.get(address)
            if feed is None:
                html_url = (outline.get("htmlUrl") or "").strip()
                feed = Feed.objects.create(
                    feed_address=address, feed_link=html_url[:1000] if html_url.startswith("http") else "",
                    created_at=ts, next_scheduled_update=ts,
                )  # fmt: skip
                existing_feeds[address] = feed
            if feed.id in subscribed:
                result["feeds_existing"] += 1
                continue
            # The outline's title is shown until the first fetch decides whether it is custom.
            user_title = title[:255] if title and title != feed.title else None
            revived = Subscription.objects.filter(user=user, feed=feed).first()
            if revived is not None:
                Subscription.objects.filter(pk=revived.pk).update(
                    folder_id=parent_id, position=position, deleted_at=None, user_title=user_title
                )
            else:
                Subscription.objects.create(
                    user=user, feed=feed, folder_id=parent_id, position=position, user_title=user_title,
                    mark_read_date=ts - 86400, created_at=ts,
                )  # fmt: skip
            subscribed.add(feed.id)
            position += 1
            result["feeds_added"] += 1

    with transaction.atomic():
        walk(body, None)
    return result


def export_opml(user) -> bytes:
    """OPML 2.0 with the user's tree in display order."""
    root = etree.Element("opml", version="2.0")
    head = etree.SubElement(root, "head")
    etree.SubElement(head, "title").text = f"{user.username}'s feeds"
    etree.SubElement(head, "dateCreated").text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    body = etree.SubElement(root, "body")
    folders: dict = {}
    for folder in tree.live_folders(user):
        folders.setdefault(folder.parent_id, []).append(folder)
    subs: dict = {}
    for sub in tree.live_subs(user).select_related("feed"):
        subs.setdefault(sub.folder_id, []).append(sub)

    def write(element, parent_id):
        items = folders.get(parent_id, []) + subs.get(parent_id, [])
        for item in sorted(items, key=lambda i: (i.position, isinstance(i, Subscription), i.id)):
            if isinstance(item, Folder):
                write(etree.SubElement(element, "outline", text=item.title, title=item.title), item.id)
            else:
                title = item.user_title or item.feed.title or item.feed.feed_address
                attributes = {"type": "rss", "text": title, "title": title, "xmlUrl": item.feed.feed_address}
                if item.feed.feed_link:
                    attributes["htmlUrl"] = item.feed.feed_link
                etree.SubElement(element, "outline", **attributes)

    write(body, None)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
