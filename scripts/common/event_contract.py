"""The contract between a region module and the ranking build (spsp_scripts).

The build reads derived.json / users_derived.jsonl / geo.json and a few attributes of the region modules, and it
does not substitute defaults for anything missing. So every region module must produce every key listed here,
even when the region has no such rule yet (then the value is the "no rule" value: False / None / 0). derive.py
checks this before writing and stops on a violation, so a gap shows up here and not as a KeyError in the build.

  names       flags from the tournament / event name (all bool)
  calendar    the event's dates and calendar flags (None only when the event has no timestamp)
  naming      labels derived from the tournament name (series, numbering, award labels, cancelled / test page)
  place       geo: the venue's geographic unit (a geo.json unit id, or None)
  classify.py TIMEZONE, CLASSIFIER_VERSION, SMACOMI_FORCE_WEEKDAY_MAX_NENT, FORCE_WEEKEND_MIN_NENT
  naming.py   tournament_series / tournament_series_number / tournament_award_label / tournament_individual_label,
              CANCELLED_PATTERN / TEST_PATTERN (used for tournaments that have no derived.json yet: upcoming lists, indexes)
  country.py  country_ja (display name of a country), is_overseas_country (outside the region), COUNTRY_CODES (start.gg
              country codes that belong to the region; events elsewhere are left out of the region's index)
  geo.py      catalog() (see scripts/common/geo.py)
"""
from __future__ import annotations

import importlib

NAMES_KEYS = frozenset({
    "special_rules", "uchi", "non_serious", "restricted_tname", "restricted_ename", "lower_class",
    "pre", "smapa", "force_weekday", "smacomi",
})
CALENDAR_KEYS = frozenset({"date", "end_date", "is_weekend_real", "is_force_weekend_period"})
NAMING_KEYS = frozenset({"series", "series_number", "award_label", "individual_label", "cancelled", "test_page"})
PLACE_KEYS = frozenset({"geo"})
CLASSIFY_ATTRS = ("TIMEZONE", "CLASSIFIER_VERSION", "SMACOMI_FORCE_WEEKDAY_MAX_NENT", "FORCE_WEEKEND_MIN_NENT",
                  "classify_event", "classify_user")
NAMING_ATTRS = ("tournament_series", "tournament_series_number", "tournament_award_label",
                "tournament_individual_label", "naming_labels", "CANCELLED_PATTERN", "TEST_PATTERN")
COUNTRY_ATTRS = ("country_ja", "is_overseas_country", "COUNTRY_CODES")
GEO_ATTRS = ("catalog",)


def check_event(cur: dict) -> list[str]:
    """Contract violations of one derived.json content (empty = fine)."""
    errs = []
    for key, req in (("names", NAMES_KEYS), ("naming", NAMING_KEYS), ("place", PLACE_KEYS)):
        d = cur.get(key)
        if not isinstance(d, dict):
            errs.append(f"{key}: missing or not a dict")
            continue
        missing = sorted(req - set(d))
        if missing:
            errs.append(f"{key}: missing {missing}")
    cal = cur.get("calendar")
    if cal is not None:
        if not isinstance(cal, dict):
            errs.append("calendar: not a dict")
        else:
            missing = sorted(CALENDAR_KEYS - set(cal))
            if missing:
                errs.append(f"calendar: missing {missing}")
    elif "calendar" not in cur:
        errs.append("calendar: missing")
    names = cur.get("names")
    if isinstance(names, dict):
        bad = [k for k in NAMES_KEYS if k in names and not isinstance(names[k], bool)]
        if bad:
            errs.append(f"names: not bool {sorted(bad)}")
    return errs


def check_region_modules(region: str) -> list[str]:
    """Contract violations of scripts/<region>/{classify,naming,country,geo}.py (empty = fine)."""
    errs = []
    mod = region.replace(" ", "_")
    for name, attrs in (("classify", CLASSIFY_ATTRS), ("naming", NAMING_ATTRS), ("country", COUNTRY_ATTRS), ("geo", GEO_ATTRS)):
        try:
            m = importlib.import_module(f"scripts.{mod}.{name}")
        except ImportError as e:
            errs.append(f"scripts/{mod}/{name}.py: cannot import ({e})")
            continue
        for a in attrs:
            if not hasattr(m, a):
                errs.append(f"scripts/{mod}/{name}.py: missing {a}")
    return errs
