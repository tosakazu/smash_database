# -*- coding: utf-8 -*-
"""North America: labels derived from tournament names (the region's naming module).

PROVISIONAL (2026-09-16). The build needs a series name (grouping recurring tournaments), a series number, an
award label (achievements) and an individual label for every event, plus patterns for cancelled and test pages.
Japan has hand-curated tables for this; North America starts with a plain rule that strips a trailing number
("Smash Night #12", "Weekly 45", "Vol. 3", "Week 5") and treats the rest as the series. The operator should
replace it with real knowledge (series aliases, editions, majors) as it becomes available.
"""
from __future__ import annotations

import re

# ... "#12", "No. 12", "Vol. 3", "Volume 3", "Week 5", "Edition 4", "Episode 7", "12", "12.5" at the end of the name
_TRAILING_NUMBER_RE = re.compile(
    r'(?:\s*[#\-:]|\s+(?:no|vol|volume|week|wk|edition|ed|episode|ep|round|season)\.?)?\s*#?(\d+(?:\.\d+)?)\s*$',
    re.IGNORECASE,
)
CANCELLED_PATTERN = re.compile(r'\b(?:cancel+ed|cancellation)\b|\(cancel+ed\)', re.IGNORECASE)
TEST_PATTERN = re.compile(r'\btest(?:ing)?\b(?!\s*(?:of|your))', re.IGNORECASE)


def tournament_series_number(name: str) -> int | float | None:
    """The trailing edition number of a tournament name (None if the name has none)."""
    m = _TRAILING_NUMBER_RE.search(name or '')
    if not m:
        return None
    num = m.group(1)
    return float(num) if '.' in num else int(num)


def tournament_series(name: str) -> str:
    """The recurring series a tournament belongs to: the name without its trailing number (provisional)."""
    name = (name or '').strip()
    if tournament_series_number(name) is None:
        return name
    stem = _TRAILING_NUMBER_RE.sub('', name).strip(' -:#')
    return stem or name


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
    }
