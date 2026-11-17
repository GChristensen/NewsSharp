"""Preference catalog (accounts/prefs.py): names, validators and defaults of item 19 (R19.8)."""
from zoneinfo import ZoneInfo


def _enum(*values):
    return lambda v: isinstance(v, str) and v in values


def _bool(v):
    return isinstance(v, bool)


def _int(low, high):
    return lambda v: type(v) is int and low <= v <= high


def _timezone(v):
    if not isinstance(v, str) or not v:
        return False
    try:
        ZoneInfo(v)
        return True
    except Exception:
        return False


def _summary_width(v):
    """0 means no limit; otherwise a width in pixels."""
    return type(v) is int and (v == 0 or SUMMARY_WIDTH_MIN <= v <= SUMMARY_WIDTH_MAX)


def _url_or_empty(v):
    return isinstance(v, str) and len(v) <= 500 and (v == "" or v.startswith(("http://", "https://")))


# Story sharing services, in the order of the share menu (frontend/src/utils/share.ts). NewsBlur's
# Pocket, Delicious, Google+ and Evernote are left out: those services or their share pages no longer exist.
SHARE_SERVICES = (
    "copyurl", "copytext", "print", "email",
    "facebook", "twitter", "bluesky", "mastodon", "reddit", "hackernews", "linkedin", "tumblr", "blogger",
    "livejournal", "pinterest", "flipboard", "buffer", "whatsapp", "telegram", "lineme",
    "instapaper", "readwise", "pinboard", "raindrop", "diigo",
)


def _share_services(v):
    return isinstance(v, list) and len(set(v)) == len(v) and all(isinstance(s, str) and s in SHARE_SERVICES for s in v)


_SIZES = ("xs", "s", "m", "l", "xl")
STORY_FONTS = ("sans-serif", "serif", "system", "georgia", "palatino", "helvetica", "segoe", "verdana", "monospace")

VIEWS = ("original", "feed", "text", "story")
LAYOUTS = ("full", "split", "list", "grid", "magazine")
ORDERS = ("newest", "oldest")
READ_FILTERS = ("all", "unread")
GRID_COLUMNS = ("1", "2", "3", "4", "5", "6")
CONTENT_PREVIEWS = ("title", "small", "medium", "large")
IMAGE_PREVIEWS = ("none", "small-left", "small-right", "large-left", "large-right")
# Full layout: "images" shows only the story bodies, without titles or any story controls.
STORY_DISPLAYS = ("standard", "images")
# Maximum width of the content preview in List and Magazine, in pixels (0: no limit).
SUMMARY_WIDTH_MIN = 300
SUMMARY_WIDTH_MAX = 1600

# name -> (validator, default)
CATALOG = {
    "default_view": (_enum(*VIEWS), "feed"),
    "story_layout": (_enum(*LAYOUTS), "split"),
    "default_order": (_enum(*ORDERS), "newest"),
    "default_read_filter": (_enum(*READ_FILTERS), "all"),
    "feed_pane_size": (_int(225, 2000), 278),
    "sidebar_collapsed": (_bool, False),
    "story_titles_pane_size": (_int(68, 4000), 420),
    "story_pane_anchor": (_enum("west", "north", "south"), "west"),
    "feed_view_single_story": (_bool, False),
    "full_width_story": (_bool, False),
    "story_position": (_enum("left", "center", "right", "stretch"), "center"),
    "density": (_enum("compact", "comfortable"), "comfortable"),
    "show_content_preview": (_enum(*CONTENT_PREVIEWS), "medium"),
    "image_preview": (_enum(*IMAGE_PREVIEWS), "small-left"),
    "grid_columns": (_int(1, 6), 4),
    "grid_height": (_enum(*_SIZES), "m"),
    "summary_max_width": (_summary_width, 0),
    "story_display": (_enum(*STORY_DISPLAYS), "standard"),
    "story_styling": (_enum(*STORY_FONTS), "sans-serif"),
    "story_size": (_enum(*_SIZES), "m"),
    "story_line_spacing": (_enum(*_SIZES), "m"),
    "feed_size": (_enum(*_SIZES), "m"),
    "folder_counts": (_bool, False),
    "title_counts": (_bool, True),
    "sidebar_mode": (_enum("all", "unread", "saved"), "all"),
    "show_feed_filter": (_bool, False),
    "show_all_site_stories": (_bool, False),
    "theme": (_enum("auto", "light", "dark"), "dark"),
    "dateformat": (_enum("12", "24"), "24"),
    "timezone": (_timezone, "UTC"),
    "days_of_unread": (_int(1, 365), 30),
    "read_story_delay": (_int(-1, 60), 0),
    "mark_read_on_scroll_titles": (_bool, False),
    "open_feed_action": (_enum("newest", "none"), "newest"),
    "markread_nextfeed": (_enum("nextfeed", "nothing"), "nextfeed"),
    "mark_read_river_confirm": (_bool, True),
    "new_window": (_bool, True),
    "keyboard_verticalarrows": (_enum("story", "scroll"), "story"),
    "keyboard_horizontalarrows": (_enum("view", "site"), "view"),
    "space_bar_action": (_enum("next_unread", "scroll_only"), "next_unread"),
    "space_scroll_spacing": (_int(10, 100), 40),
    "arrow_scroll_spacing": (_int(10, 500), 100),
    "animations": (_bool, True),
    "show_tooltips": (_bool, True),
    "feedxcavator_url": (_url_or_empty, ""),
    "feedxcavator_newspaper_url": (_url_or_empty, ""),
    "share_services": (_share_services, []),
}

VIEW_SETTING_KEYS = {
    "view": VIEWS,
    "layout": LAYOUTS,
    "order": ORDERS,
    "read_filter": READ_FILTERS,
    "grid_columns": GRID_COLUMNS,
    "show_content_preview": CONTENT_PREVIEWS,
    "image_preview": IMAGE_PREVIEWS,
    "story_display": STORY_DISPLAYS,
}


def invalid_keys(changes: dict) -> list[str]:
    bad = []
    for key, value in changes.items():
        entry = CATALOG.get(key)
        if entry is None or not entry[0](value):
            bad.append(key)
    return bad


def effective(stored: dict | None) -> dict:
    """Stored preferences completed with defaults; invalid stored values fall back to defaults."""
    stored = stored or {}
    result = {}
    for key, (validator, default) in CATALOG.items():
        value = stored.get(key, default)
        value = value if validator(value) else default
        # A list default must not be shared between users.
        result[key] = list(value) if isinstance(value, list) else value
    return result
