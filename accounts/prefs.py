"""Preference catalog (accounts/prefs.py): names, validators and defaults of every preference."""
import re
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


# Story filters (filter_promote, filter_demote, filter_hide): one regular expression per line, matched
# without regard to case against a story's title, author and summary (reading/filters.py).
FILTER_LINES_MAX = 200
FILTER_LINE_CHARS = 500


def combined_pattern(lines: list[str]) -> str | None:
    """One case-insensitive regular expression matching any of the lines; None for no lines."""
    if not lines:
        return None
    return "(?i)" + "|".join(f"(?:{line})" for line in lines)


def valid_patterns(v) -> bool:
    """A list of filter lines: each a regular expression, also valid when combined with the others."""
    if not isinstance(v, list) or len(v) > FILTER_LINES_MAX:
        return False
    for line in v:
        if not isinstance(line, str) or not line.strip() or line != line.strip() or len(line) > FILTER_LINE_CHARS:
            return False
    try:
        for line in v:
            re.compile(line, re.IGNORECASE)
        if v:
            re.compile(combined_pattern(v))  # a global flag such as (?i) is valid alone but not mid-pattern
    except (re.error, RecursionError, OverflowError):
        return False
    return True


FILTER_KINDS = ("promote", "demote", "hide")


def valid_feed_filters(v) -> bool:
    """The filters of one subscription: {kind: lines} for some of FILTER_KINDS."""
    return isinstance(v, dict) and all(kind in FILTER_KINDS and valid_patterns(lines) for kind, lines in v.items())


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
# Variants of the dark theme (data-tint in frontend/src/styles.css); "graphite" is the base dark theme.
DARK_TINTS = ("graphite", "oled", "ink", "navy", "warm", "moss", "teal", "plum", "ember", "rose", "dim")
# Maximum width of the content preview in List and Magazine, in pixels (0: no limit). The preference
# summary_max_width is the default of the per-scope view setting summary_width.
SUMMARY_WIDTH_MIN = 300
SUMMARY_WIDTH_MAX = 1600
SUMMARY_WIDTHS = ("0", *(str(v) for v in range(SUMMARY_WIDTH_MIN, SUMMARY_WIDTH_MAX + 1)))
# Distance of the headlines from the left edge in List and Magazine, in pixels; a per-scope setting.
HEADLINE_INDENT_MAX = 400
HEADLINE_INDENTS = tuple(str(v) for v in range(HEADLINE_INDENT_MAX + 1))

# Color adjustments of each theme (preference theme_colors): theme -> {name: value}; a missing name keeps
# the theme's own color. Ranges: vividness is a percentage of the accent chroma, accent_hue a turn in
# degrees, bg_hue and bg_strength the hue and amount of the background color (set together; they replace
# the theme's own cast), text_contrast a lightness shift towards more (+) or less (-) contrast.
THEME_COLOR_RANGES = {
    "accent_vividness": (30, 170),
    "accent_hue": (-30, 30),
    "bg_hue": (0, 359),
    "bg_strength": (0, 100),
    "text_contrast": (-50, 50),
}
THEMES = ("light", *DARK_TINTS)


def _theme_colors(v):
    if not isinstance(v, dict):
        return False
    for theme, values in v.items():
        if theme not in THEMES or not isinstance(values, dict) or not values:
            return False
        if ("bg_hue" in values) != ("bg_strength" in values):
            return False
        for key, value in values.items():
            limits = THEME_COLOR_RANGES.get(key)
            if limits is None or not _int(*limits)(value):
                return False
    return True


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
    "headline_indent": (_int(0, HEADLINE_INDENT_MAX), 0),
    "story_display": (_enum(*STORY_DISPLAYS), "standard"),
    "story_styling": (_enum(*STORY_FONTS), "sans-serif"),
    "story_size": (_enum(*_SIZES), "m"),
    "story_line_spacing": (_enum(*_SIZES), "m"),
    "feed_size": (_enum(*_SIZES), "m"),
    "folder_counts": (_bool, False),
    "title_counts": (_bool, True),
    "sidebar_mode": (_enum("all", "unread", "saved"), "all"),
    "show_feed_filter": (_bool, False),
    "show_all_site_stories": (_bool, True),
    "theme": (_enum("auto", "light", "dark"), "dark"),
    "dark_tint": (_enum(*DARK_TINTS), "graphite"),
    "theme_colors": (_theme_colors, {}),
    "dateformat": (_enum("12", "24"), "24"),
    "timezone": (_timezone, "UTC"),
    "days_of_unread": (_int(1, 365), 30),
    "read_story_delay": (_int(-1, 60), 0),
    # Off by default in every layout; opening a story is then the only way scrolling marks it read.
    "mark_read_on_scroll": (_bool, False),
    "open_feed_action": (_enum("newest", "none"), "newest"),
    # What `/` shows when the app is opened: the start page, All Site Stories or the last open scope.
    "start_page": (_enum("home", "all", "last"), "home"),
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
    "filter_promote": (valid_patterns, []),
    "filter_demote": (valid_patterns, []),
    "filter_hide": (valid_patterns, []),
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
    "headline_indent": HEADLINE_INDENTS,
    "summary_width": SUMMARY_WIDTHS,
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
        # A list or dict default must not be shared between users.
        result[key] = list(value) if isinstance(value, list) else dict(value) if isinstance(value, dict) else value
    return result
