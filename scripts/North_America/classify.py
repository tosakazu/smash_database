# -*- coding: utf-8 -*-
"""North America classification rules (the region module that derive.py loads).

This is not a copy of Japan (scripts/Japan/classify.py): it contains **only what has been verified for this region**.
The Japan-specific calendar (treating Obon and the New Year period as holidays) and Japan's tournament-name patterns (restricted tournaments, lower-class brackets, pre-tournaments)
are not here. If they are needed, the North American operator should add them. After adding anything, bump CLASSIFIER_VERSION and
run `derive.py --region North_America --all` (this regenerates derived.json for every event).

What currently goes into derived.json:
  is_1on1 / not_1on1_reason   whether the event was excluded by name (doubles, team events, amiibo, and so on)
  calendar                    the event date (local time), whether it falls on a weekend, and whether it is a public holiday in the host country (US / CA / MX / DO)
  class_bracket               the phase groups of lower-class side brackets held inside a tournament (Redemption / Amateur, etc.)
  names.lower_class           whether the event itself is a lower-class bracket (an event literally named "Redemption Bracket", for example)
  names.restricted            tournaments whose entry pool is restricted (Arcadian = players on the regional Power Ranking may not enter).
                              This corresponds to Japan's restricted tournaments (rating caps). They are still aggregated, but handled differently

Not yet implemented (add it to this file when needed):
  periods treated as holidays (the equivalent of Japan's Obon and New Year period; what should count as such in North America is undecided),
  state/provincial holidays (Family Day, etc. attr.place does not carry the state, so a mechanism to extract the state from venue_address
  is needed first), labels derived from tournament names (series names, restricted tournaments, and so on),
  the state where the venue is located, and players' places of residence.

The names used for class brackets were checked against one week of US data (2026-09-05 to 2026-09-11, 513 events):
by far the most common is **Redemption** (a second-chance bracket for eliminated players; it appears both as a phase and as a separate event "Redemption
Bracket"). Amateur / Novice did not appear at all (they are kept on the assumption that large majors use them).
The order of CLASS_LETTERS is used to number virtual event IDs, so never reorder it afterwards (append new entries at the end).

Points not yet fully verified (the operator should check these against the local calendar):
  - The DO Monday shift (ley 139-97) is implemented as "Tue/Wed -> the preceding Monday, Thu/Fri/Sat -> the following Monday";
    holidays that fall on a Sunday are left in place.
  - Some sources say DO's 8/16 (Restauración) stays on its fixed date in presidential inauguration years; this is not distinguished.
"""
from __future__ import annotations

import datetime as dt
import functools
import re

from scripts.common.region import class_phase_group_ids

CLASSIFIER_VERSION = 8   # 8: lower_class is now also checked on the tournament name (same as Japan; for tournaments that are lower-class as a whole, like "Novice Knockout")
                         # 7: Arcadian (tournaments that players on the PR may not enter) is no longer excluded from 1on1; it is flagged via names.restricted instead
                         # 6: Redemption (second-chance bracket) is treated as a class bracket, names.lower_class added (verified on one week of real US data)
                         # 5: is_offline (the common part in derive.py) added
                         #  # 4: class brackets (Amateur / Novice / B-E class) handled
                         # 3: DO holiday table (Monday shift) and the Mexican inauguration day; countries without a table get no holidays (recorded in holidays)
                         # 2: public holidays (US / CA / MX) added as weekend-equivalent days   # 1: initial version (1on1 check and calendar only)

# North America spans several time zones. Use the event's place.timezone when present,
# and fall back to this default only when it is missing (derive.py also sets the process TZ to this value).
TIMEZONE = "America/New_York"


def check_requirements() -> None:
    """External packages needed by this region's classifier. None at the moment (check here if a holiday library is ever added)."""
    return None


# ── 1on1 check (decided from tournament name and event name only) ──
# Exclusion words. Besides English, Canadian French and Mexican Spanish spellings are included.
EXCLUDE_PATTERNS = (
    "doubles", "dubs", "2v2", "teams", "team event",
    "crew battle", "crews", "squad strike", "squadstrike",
    "amiibo", "ladder",
    "dobles", "equipos",                     # es
    "doublette", "équipes", "equipes",       # fr
    "test", "testing",
)
# N-vs-N notations such as 2on2 / 3v3 / 5 vs 5
EXCLUDE_REGEX = re.compile(r'(?<!\w)[2-9]\s*[-‐ ]?\s*(?:v|vs|on)\s*[-‐ ]?\s*[2-9](?!\w)', re.IGNORECASE)
# Tournaments treated as 1on1 even though they contain an exclusion word (add them as they are found)
ALLOW_PATTERNS: tuple[str, ...] = ()


def is_1on1_event(attr: dict) -> tuple[bool, str | None]:
    """(treat as 1on1?, reason for rejection). The reason is "keyword:<word>" / "regex:NvN"."""
    haystack_orig = (attr.get("event_name") or "") + " | " + (attr.get("tournament_name") or "")
    haystack = haystack_orig.lower()
    for kw in ALLOW_PATTERNS:
        if kw.lower() in haystack:
            return True, None
    for kw in EXCLUDE_PATTERNS:
        if kw.lower() in haystack:
            return False, f"keyword:{kw}"
    if EXCLUDE_REGEX.search(haystack_orig):
        return False, "regex:NvN"
    return True, None


# ── Calendar ──
# National public holidays. They differ by country, so they are looked up by host country (attr.place.country_code). State/provincial holidays are not included
# (Family Day, the holidays of individual Mexican states, etc. If they become necessary, look at the state as well, not just country_code).
# Notation: ("fixed", month, day) / ("nth", month, weekday 0=Mon, n-th; -1 = last) / ("easter", days offset from Easter Sunday)
#      / ("movable", month, day) = holiday shifted to a Monday (DO) / ("transmission",) = the Mexican presidential inauguration
# Countries without a table (when a new country joins this region) get no holidays. Never substitute another country's table.
DEFAULT_COUNTRY = "US"      # for the default time zone when place.timezone is missing (not a default for holidays)

HOLIDAY_RULES: dict[str, tuple] = {
    "US": (
        ("fixed", 1, 1),            # New Year's Day
        ("nth", 1, 0, 3),           # Martin Luther King Jr. Day
        ("nth", 2, 0, 3),           # Presidents' Day
        ("nth", 5, 0, -1),          # Memorial Day
        ("fixed", 6, 19),           # Juneteenth
        ("fixed", 7, 4),            # Independence Day
        ("nth", 9, 0, 1),           # Labor Day
        ("nth", 10, 0, 2),          # Columbus Day
        ("fixed", 11, 11),          # Veterans Day
        ("nth", 11, 3, 4),          # Thanksgiving
        ("fixed", 12, 25),          # Christmas Day
    ),
    "CA": (
        ("fixed", 1, 1),            # New Year's Day
        ("easter", -2),             # Good Friday
        ("victoria",),              # Victoria Day (the Monday immediately before 5/25)
        ("fixed", 7, 1),            # Canada Day
        ("nth", 9, 0, 1),           # Labour Day
        ("fixed", 9, 30),           # National Day for Truth and Reconciliation
        ("nth", 10, 0, 2),          # Thanksgiving
        ("fixed", 11, 11),          # Remembrance Day
        ("fixed", 12, 25),          # Christmas Day
        ("fixed", 12, 26),          # Boxing Day
    ),
    "MX": (
        ("fixed", 1, 1),            # Año Nuevo
        ("nth", 2, 0, 1),           # Día de la Constitución
        ("nth", 3, 0, 3),           # Natalicio de Benito Juárez
        ("fixed", 5, 1),            # Día del Trabajo
        ("fixed", 9, 16),           # Día de la Independencia
        ("nth", 11, 0, 3),          # Revolución Mexicana
        ("transmission",),          # 12/1 presidential inauguration (every 6 years)
        ("fixed", 12, 25),          # Navidad
    ),
}
HOLIDAY_RULES["DO"] = (
    ("fixed", 1, 1),            # Año Nuevo
    ("movable", 1, 6),          # Día de Reyes
    ("fixed", 1, 21),           # Nuestra Señora de la Altagracia
    ("movable", 1, 26),         # Día de Duarte
    ("fixed", 2, 27),           # Día de la Independencia
    ("easter", -2),             # Viernes Santo
    ("easter", 60),             # Corpus Christi
    ("movable", 5, 1),          # Día del Trabajo
    ("movable", 8, 16),         # Día de la Restauración
    ("fixed", 9, 24),           # Nuestra Señora de las Mercedes
    ("movable", 11, 6),         # Día de la Constitución
    ("fixed", 12, 25),          # Navidad
)

# Countries that move a holiday falling on a weekend to the adjacent weekday (the observed day is also a day off = tournaments are likely to be scheduled)
OBSERVED_SHIFT_COUNTRIES = ("US", "CA")

# Years in which 12/1 is a holiday (the Mexican presidential inauguration; every 6 years, most recently 2024)
MX_TRANSMISSION_BASE_YEAR = 2024

# To add dates the rules above do not produce (one-off holidays, weeks of major events, etc.), write the actual dates here
EXTRA_HOLIDAY_DATES: frozenset[dt.date] = frozenset()


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The n-th <weekday> of the month (the last one if n = -1)."""
    if n < 0:
        d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year, 12, 31)
        while d.weekday() != weekday:
            d -= dt.timedelta(days=1)
        return d
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(days=7 * (n - 1))


def _easter(year: int) -> dt.date:
    """Western (Gregorian) Easter Sunday (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return dt.date(year, month, day + 1)


def _movable_to_monday(d: dt.date) -> dt.date:
    """Dominican Republic (ley 139-97): Tue/Wed move to the preceding Monday, Thu/Fri/Sat to the following Monday.
    The handling of a Sunday has not been confirmed, so it is left in place (needs verification)."""
    wd = d.weekday()
    if wd in (1, 2):                                  # Tue / Wed
        return d - dt.timedelta(days=wd)
    if wd in (3, 4, 5):                               # Thu / Fri / Sat
        return d + dt.timedelta(days=7 - wd)
    return d


def holidays_source(country_code: str | None) -> str | None:
    """Whether a holiday table exists for the country (None if not = no holidays are applied; never substitute another country's table)."""
    cc = (country_code or "").upper()
    return cc if cc in HOLIDAY_RULES else None


@functools.lru_cache(maxsize=None)
def holidays_for(country_code: str | None, year: int) -> frozenset[dt.date]:
    """Public holidays of the country for that year (including observed days). Empty for countries without a table (no guessing)."""
    cc = holidays_source(country_code)
    if cc is None:
        return frozenset(EXTRA_HOLIDAY_DATES)
    days: set[dt.date] = set()
    for rule in HOLIDAY_RULES[cc]:
        kind = rule[0]
        if kind == "fixed":
            days.add(dt.date(year, rule[1], rule[2]))
        elif kind == "nth":
            days.add(_nth_weekday(year, rule[1], rule[2], rule[3]))
        elif kind == "easter":
            days.add(_easter(year) + dt.timedelta(days=rule[1]))
        elif kind == "victoria":
            d = dt.date(year, 5, 25) - dt.timedelta(days=1)
            while d.weekday() != 0:
                d -= dt.timedelta(days=1)
            days.add(d)
        elif kind == "movable":
            days.add(_movable_to_monday(dt.date(year, rule[1], rule[2])))
        elif kind == "transmission":
            if (year - MX_TRANSMISSION_BASE_YEAR) % 6 == 0:
                days.add(dt.date(year, 12, 1))
    if cc in OBSERVED_SHIFT_COUNTRIES:
        for d in list(days):
            if d.weekday() == 5:
                days.add(d - dt.timedelta(days=1))    # Saturday -> the preceding Friday
            elif d.weekday() == 6:
                days.add(d + dt.timedelta(days=1))    # Sunday -> the following Monday
    return frozenset(days | EXTRA_HOLIDAY_DATES)


def is_weekend_date(d: dt.date, country_code: str | None = None) -> bool:
    """Saturday/Sunday, or a public holiday of the host country (including observed days). Weekends only for countries without a table."""
    return d.weekday() >= 5 or d in holidays_for(country_code, d.year)


def is_weekend_range(start_d: dt.date, end_d: dt.date, country_code: str | None = None) -> bool:
    """True if any day from the start date to the end date is a weekend. Long events of more than 15 days are judged by the start date only
    (because endAt sometimes extends until the bracket is closed. Same convention as Japan)."""
    if end_d < start_d:
        end_d = start_d
    delta = (end_d - start_d).days
    if delta > 14:
        return is_weekend_date(start_d, country_code)
    return any(is_weekend_date(start_d + dt.timedelta(days=i), country_code) for i in range(delta + 1))


def _zone(place: dict | None):
    """The event's local time zone: place.timezone if present, otherwise the region default TIMEZONE."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    name = (place or {}).get("timezone") or TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(TIMEZONE)


def calendar_flags(timestamp: int, end_timestamp: int | None, place: dict | None = None) -> dict:
    """The event date (local time) and whether it is a weekend or public holiday. Holidays are those of the host country.
    is_force_weekend_period (Japan's Obon / New Year period) has no North American equivalent defined yet, so it is not included."""
    tz = _zone(place)
    cc = (place or {}).get("country_code")
    d = dt.datetime.fromtimestamp(int(timestamp), tz).date()
    end_ts = end_timestamp if end_timestamp is not None else timestamp
    end_d = dt.datetime.fromtimestamp(int(end_ts), tz).date()
    return {
        "date": d.isoformat(),
        "end_date": end_d.isoformat(),
        "timezone": str(tz),
        "country_code": cc,
        "holidays": holidays_source(cc),      # which country's holiday table was applied (None = none applied)
        "is_weekend_real": is_weekend_range(d, end_d, cc),
    }


# ── Class brackets (lower-class side brackets held inside a tournament) ──
# The counterpart of Japan's B/C/D/E classes. In North America the names Amateur / Novice etc. are common (needs verification).
# The order of CLASS_LETTERS = the offset used for virtual event IDs. Never reorder it afterwards (append at the end).
CLASS_LETTERS = ("AMATEUR", "NOVICE", "BEGINNER", "B", "C", "D", "E", "REDEMPTION")

CLASS_PHASE_PATTERN = re.compile(
    r'(?<![A-Za-z])(?:amateur|amateurs|novice|beginner|redemption)(?![A-Za-z])'
    r'|(?<![A-Za-z])[BCDE][\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)
CLASS_LETTER_PATTERN = re.compile(
    r'(?<![A-Za-z])(amateur|novice|beginner|redemption)s?(?![A-Za-z])'
    r'|(?<![A-Za-z])([BCDE])[\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)

# Events that are themselves a lower-class bracket (e.g. a separate event named "Redemption Bracket" / "Ultimate Redemption").
# The counterpart of Japan's names.lower_class ("B class only", etc.). Flagged so they can be handled separately from the main 1on1 bracket
LOWER_CLASS_EVENT_PATTERN = re.compile(
    r'(?<![A-Za-z])(?:redemption|amateur|amateurs|novice|beginner)(?![A-Za-z])'
    r'|(?<![A-Za-z])[BCDE][\s_\-]*class(?![A-Za-z])',
    re.IGNORECASE,
)


# Tournaments whose entry conditions restrict the field. Arcadian = players listed on the regional Power Ranking may not enter (a tournament without the top players).
# This corresponds to Japan's "rating below 1700" restriction, so instead of removing it from 1on1 it is flagged as restricted
RESTRICTED_PATTERN = re.compile(r'(?<![A-Za-z])arcadian(?![A-Za-z])', re.IGNORECASE)


def name_flags(tname: str, ename: str) -> dict:
    """Flags decided from the tournament name and event name. As in Japan, restricted is kept separately for the tournament name and the event name
    (so that a main bracket held at the same time is not swept in). Pre-tournaments, special rules, etc. are not defined yet."""
    return {
        "lower_class": bool(LOWER_CLASS_EVENT_PATTERN.search(tname or '') or LOWER_CLASS_EVENT_PATTERN.search(ename or '')),
        "restricted_tname": bool(RESTRICTED_PATTERN.search(tname or '')),
        "restricted_ename": bool(RESTRICTED_PATTERN.search(ename or '')),
    }


def is_class_phase(name: str | None) -> bool:
    """Whether the phase name denotes a class bracket (is_class in phases.json)."""
    return bool(CLASS_PHASE_PATTERN.search(name or ''))


def is_unseparated_class_phase(name: str | None) -> bool:
    """Whether the phase name in matches.json denotes a class bracket (used to detect phases not yet separated in phases.json). The same check is fine for North America."""
    return is_class_phase(name)


def class_letter(name: str | None) -> str | None:
    """phase name -> class identifier (AMATEUR / NOVICE / BEGINNER / B / C / D / E)."""
    if not name:
        return None
    m = CLASS_LETTER_PATTERN.search(name)
    if not m:
        return None
    return (m.group(1) or m.group(2)).upper()


def class_virtual_event_name(event_name: str, letter: str) -> str:
    """The event name used when a class is split out as a tournament of its own."""
    label = f"{letter} class" if len(letter) == 1 else letter.title()
    return f"{event_name} / {label}"


# ── Annotations for upcoming tournaments. For tournaments that have no directory yet, classify from the name and start date only ──
def upcoming_flags(tournament_name: str, event_name: str, num_entrants: int, start_ts: int | None) -> dict:
    """Classification attached to one upcoming tournament. For North America only the 1on1 check and the calendar (no labels from tournament names yet).
    The host country is unknown (upcoming entries have no place), so holidays are not applied; only Saturday/Sunday is checked."""
    ok, reason = is_1on1_event({"tournament_name": tournament_name, "event_name": event_name})
    out = {"is_1on1": ok, "not_1on1_reason": reason}
    if start_ts:
        start = dt.datetime.fromtimestamp(int(start_ts)).date()
        out["is_weekend_real"] = start.weekday() >= 5
        out["is_weekend"] = out["is_weekend_real"]
    else:
        out["is_weekend"] = False       # unknown start date is conservatively treated as a weekday
        out["is_weekend_real"] = False
    return out


# ── Entry point called from derive.py ──
def classify_event(base: dict, ctx) -> dict:
    """Add the North American classification to base (the common part built by derive.py) and return it. ctx: attr / tname / ename / phases / class_phase_files."""
    ok, reason = is_1on1_event(ctx.attr)
    ts = ctx.attr.get("timestamp")
    all_class, pg_ids = class_phase_group_ids(ctx.phases, ctx.class_phase_files)
    out = dict(base)
    out.update({
        "is_1on1": ok,
        "not_1on1_reason": reason,
        "calendar": (calendar_flags(ts, ctx.attr.get("end_timestamp"), ctx.attr.get("place"))
                     if ts is not None else None),
        "names": name_flags(ctx.tname, ctx.ename),
        "class_bracket": {"all_phases_class": all_class, "phase_group_ids": pg_ids},
    })
    return out


def classify_user(u: dict) -> dict | None:
    """One line of users.jsonl -> the fields to derive. North America derives nothing yet (None = no line is written to users_derived.jsonl)."""
    return None
