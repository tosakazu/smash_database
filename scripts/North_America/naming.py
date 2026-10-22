# -*- coding: utf-8 -*-
"""North America: labels derived from tournament names (the region's naming module).

PROVISIONAL (2026-09-16). The build needs a series name (grouping recurring tournaments), a series number, an
award label (achievements) and an individual label for every event, plus patterns for cancelled and test pages.
Japan has hand-curated tables for this; North America starts with a plain rule. The operator should replace it
with real knowledge (series aliases, editions, majors) as it becomes available.

Revised 2026-10-22 against all 136,442 distinct North American tournament names. The original rule only
stripped a number sitting at the very end, so anything after it kept every edition apart as its own series:
"West Towne Brawl #187 @ Pegasus Games" was 182 separate series, "Fight Night #93 (Las Vegas, NV)" 132, and a
trailing date did the same to "Game Underground Weekly 4/18/" (130) and "Liberty Smash Weekly #310 (2/14/25)"
(126). 17,048 tournaments were scattered this way. Dates and season markers are now removed wherever they
appear and the edition number is stripped in place rather than only at the end, which drops the series count
from 55,503 to 45,082 and one-offs from 48,423 to 36,969.

What is deliberately NOT removed is whatever else the suffix says. The number comes out of "West Towne Brawl
#187 @ Pegasus Games" but "@ Pegasus Games" stays, so two series that share a base name but run at different
venues are still two series. Checked: 108 groups that the old rule had as several multi-edition series now
merge, and every one is the same event split by month or by season ("Respawn 9/" + "Respawn 11/", "Otaku Zone
Season 14" + "Season 6").
"""
from __future__ import annotations

import re

# ... "#12", "No. 12", "Vol. 3", "Volume 3", "Week 5", "Edition 4", "Episode 7", "12", "12.5" at the end of the
# name. Still anchored at the end, because this is what reports the *number* and a number buried mid-name is
# not reliably the edition. tournament_series below strips more than this.
_TRAILING_NUMBER_RE = re.compile(
    r'(?:\s*[#\-:]|\s+(?:no|vol|volume|week|wk|edition|ed|episode|ep|round|season)\.?)?\s*#?(\d+(?:\.\d+)?)\s*$',
    re.IGNORECASE,
)

# A date anywhere in the name: "4/18/", "2/14/25", "07/22/2024", "2024-07-22", with any bracket or separator
# that was holding it. Organisers put the date in the title of a weekly as often as they put the number.
# No \b after the optional trailing separator: with one, "4/18/" backtracked and kept the final
# slash, because a word boundary cannot hold between "/" and the end of the string.
_DATE_RE = re.compile(r'[\(\[\s\-\u2013\u2014|:]*\b\d{1,2}[/.]\d{1,2}(?:[/.]\d{2,4})?[/.]?[\)\]]?'
                      r'|[\(\[\s\-\u2013\u2014|:]*\b\d{4}-\d{2}-\d{2}\b[\)\]]?')
# "(S14)", "Season 3", "S3" — a season is a division of one series, not a series of its own.
_SEASON_RE = re.compile(r'[\(\[\s]*\b(?:s(?:eason)?\.?\s*\d{1,3})\b[\)\]]?', re.IGNORECASE)
# The edition number wherever it sits, so a suffix after it no longer forks the series. The bare-number branch
# only fires before a separator or end of string, so "Smash 4 Fun" keeps its 4.
_EDITION_RE = re.compile(
    r'(?:\s*[#\-:]|\s+(?:no|vol|volume|week|wk|edition|ed|episode|ep|round)\.?)?\s*#\s*\d+(?:\.\d+)?'
    r'|(?<=\s)(?:no|vol|volume|week|wk|edition|ed|episode|ep|round)\.?\s*\d+(?:\.\d+)?(?![0-9a-z])'
    r'|(?<=\s)\d{1,4}(?:\.\d+)?(?=\s*(?:[:\-\u2013\u2014|@(\[]|$))',
    re.IGNORECASE,
)
CANCELLED_PATTERN = re.compile(r'\b(?:cancel+ed|cancellation)\b|\(cancel+ed\)', re.IGNORECASE)
TEST_PATTERN = re.compile(r'\btest(?:ing)?\b(?!\s*(?:of|your))', re.IGNORECASE)


# A name that is *only* one of these plus a number is itself the name, not edition N of a series. "Week 5:
# Melee & Ultimate" is a tournament called Week 5; "SocaSmash 42: Myrtle\'s Weekly" is edition 42 of SocaSmash.
_EDITION_WORDS = frozenset(
    "week wk day round season vol volume no num edition ed episode ep part match game set".split()
)


def _drop_edition(m: "re.Match[str]") -> str:
    """Blank out an edition marker, unless the name in front of it is an edition word on its own."""
    before = m.string[:m.start()].strip(' -\u2013\u2014:|#')
    words = before.split()
    if len(words) == 1 and words[0].strip('.').lower() in _EDITION_WORDS:
        return m.group(0)
    return ' ' if words else m.group(0)


def tournament_series_number(name: str) -> int | float | None:
    """The trailing edition number of a tournament name (None if the name has none)."""
    m = _TRAILING_NUMBER_RE.search(name or '')
    if not m:
        return None
    num = m.group(1)
    return float(num) if '.' in num else int(num)


def tournament_series(name: str) -> str:
    """The recurring series a tournament belongs to: the name with its edition markers removed.

    Removes the date, the season and the edition number, in that order, and tidies up the punctuation they
    leave behind. Everything else is kept, so a venue or city in the name still separates two series.
    """
    original = (name or '').strip()
    s = _DATE_RE.sub(' ', original)
    s = _SEASON_RE.sub(' ', s)
    s = _EDITION_RE.sub(_drop_edition, s)
    s = re.sub(r'\s{2,}', ' ', s)
    s = re.sub(r'\s+([:,;!?])', r'\1', s)        # "SocaSmash : Myrtle's" -> "SocaSmash: Myrtle's"
    s = re.sub(r'([(\[])\s+|\s+([)\]])', r'\1\2', s)
    s = re.sub(r'\s*([:\-\u2013\u2014|])\s*\1*\s*$', '', s)
    s = s.strip(' -\u2013\u2014:|.#,@([{')
    s = re.sub(r'\(\s*\)|\[\s*\]', '', s).strip()
    return s or original


def tournament_award_label(name: str) -> str:
    """Label used for achievements ("won X"): the series name (provisional)."""
    return tournament_series(name)


def tournament_individual_label(name: str) -> str:
    """Label for one specific edition: the name as written."""
    return (name or '').strip()


def is_cancelled(name: str | None) -> bool:
    return bool(CANCELLED_PATTERN.search(name or ''))


def is_test_page(name: str | None) -> bool:
    return bool(TEST_PATTERN.search(name or ''))


def community_series(strict: str) -> str:
    """The community a series belongs to (local rankings merge sibling series). Provisional: the series itself."""
    return strict


def naming_labels(tname: str, ename: str) -> dict:
    """The "naming" part of derived.json (same keys as Japan)."""
    return {
        "series": tournament_series(tname),
        "series_number": tournament_series_number(tname),
        "award_label": tournament_award_label(tname),
        "individual_label": tournament_individual_label(tname),
        "cancelled": is_cancelled(tname) or is_cancelled(ename),
        "test_page": is_test_page(tname) or is_test_page(ename),
        "series_event": event_series(tname, ename),
    }


def event_series(tname: str, ename: str) -> str:
    """The series this event belongs to (series index, local rankings). North America does not split side events
    yet, so this is tournament_series(tname)."""
    return tournament_series(tname)
