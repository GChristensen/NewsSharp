"""Story filters (reading/filters.py): the Promote, Demote and Hide patterns of a user as SQL fragments.

Each list holds regular expressions; a story matches when one of them is found in its title, author or
summary, without regard to case. Hide wins over Promote and Demote, Promote wins over Demote. A feed can have
filters of its own (Subscription.filters); the user's global filters are appended to them, so a feed
without its own lines follows the global ones alone. The fragments use SQLite's REGEXP operator, which
Django registers on its SQLite connections, and expect the story table under the alias `s`."""
from dataclasses import dataclass, field

from accounts import prefs

from .models import Subscription

# The text the patterns are matched against: title, author and summary on separate lines.
MATCHED_TEXT = "(s.title || char(10) || s.author || char(10) || s.summary)"

# Rank of a story in a list: promoted stories first, demoted ones last.
RANK_PROMOTED, RANK_NORMAL, RANK_DEMOTED = 0, 1, 2


@dataclass(frozen=True)
class Pattern:
    """The combined pattern of one filter kind: per feed with lines of its own, `default` for the others."""

    default: str | None = None
    per_feed: dict[int, str] = field(default_factory=dict)

    def empty(self) -> bool:
        return self.default is None and not self.per_feed


@dataclass(frozen=True)
class Rules:
    promote: Pattern
    demote: Pattern
    hide: Pattern


def rules_for(user) -> Rules:
    values = prefs.effective(user.preferences)
    own = Subscription.objects.filter(user=user, deleted_at__isnull=True).exclude(filters={})
    own_filters = list(own.values_list("feed_id", "filters"))

    def pattern(kind: str) -> Pattern:
        global_lines = values[f"filter_{kind}"]
        per_feed = {}
        for feed_id, filters in own_filters:
            lines = filters.get(kind) or []
            if lines:
                per_feed[feed_id] = prefs.combined_pattern(lines + global_lines)
        return Pattern(prefs.combined_pattern(global_lines), per_feed)

    return Rules(promote=pattern("promote"), demote=pattern("demote"), hide=pattern("hide"))


def _matches(pattern: Pattern) -> tuple[str, list]:
    """An SQL expression that is 1 when the story matches the pattern of its feed, else 0."""
    if not pattern.per_feed:
        return f"COALESCE({MATCHED_TEXT} REGEXP %s, 0)", [pattern.default]
    whens = " ".join("WHEN %s THEN %s" for _ in pattern.per_feed)
    params = [value for item in pattern.per_feed.items() for value in item]
    # A feed without lines of its own and without global lines gets NULL, which matches nothing.
    return f"COALESCE({MATCHED_TEXT} REGEXP (CASE s.feed_id {whens} ELSE %s END), 0)", [*params, pattern.default]


def hidden_sql(rules: Rules) -> tuple[str, list]:
    """An SQL expression that is true for a hidden story, with its parameters."""
    if rules.hide.empty():
        return "0", []
    return _matches(rules.hide)


def rank_sql(rules: Rules) -> tuple[str, list]:
    """An SQL expression giving the rank of a story (RANK_*), with its parameters."""
    branches, params = [], []
    for pattern, rank in ((rules.promote, RANK_PROMOTED), (rules.demote, RANK_DEMOTED)):
        if not pattern.empty():
            condition, condition_params = _matches(pattern)
            branches.append(f"WHEN {condition} THEN {rank}")
            params.extend(condition_params)
    if not branches:
        return str(RANK_NORMAL), []
    return f"(CASE {' '.join(branches)} ELSE {RANK_NORMAL} END)", params
