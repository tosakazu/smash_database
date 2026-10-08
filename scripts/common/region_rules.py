"""region_rules.json: what the ranking build needs from a region's rules, evaluated here, as data.

The ranking build (spsp_scripts) used to import scripts/<region>/{classify,naming,country}.py to read a few constants
and to apply the naming rules to tournament names. That meant code from a data branch ran inside the build, next to
its deploy secrets (security review 2026-10-08, H1: data-North_America is pushed by an external collaborator). Now the
region's code runs only here (in the data branch's own pipeline) and the build reads this file:

  constants         TIMEZONE, CLASSIFIER_VERSION, SMACOMI_FORCE_WEEKDAY_MAX_NENT, FORCE_WEEKEND_MIN_NENT
  country_codes     COUNTRY_CODES: start.gg country codes that belong to the region
  countries         {country as registered on start.gg (users.jsonl): {"ja": country_ja(...)}}
  tournament_names  {tournament name: {series, series_number, award_label, individual_label, cancelled, test_page}}
                    for every tournament name of the region (attr.json tournament_name and tournaments.jsonl name);
                    cancelled / test_page = CANCELLED_PATTERN / TEST_PATTERN on the tournament name alone
  series            {series name (derived.json naming.series_event): {community, non_serious, uchi, special_rules,
                    restricted}}: community = community_series(name); the flags = the region's name patterns on the
                    community name

Written by derive.py next to users.jsonl, sorted, only when it changed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FILE = "region_rules.json"
VERSION = 1


def _tournament_names(region_dir: Path, events_root: Path) -> set[str]:
    names = set()
    tj = region_dir / "tournaments.jsonl"
    if tj.exists():
        for line in tj.open(encoding="utf-8"):
            try:
                n = json.loads(line).get("name")
            except ValueError:
                continue
            if n:
                names.add(n)
    for attr in events_root.rglob("attr.json"):
        try:
            n = json.loads(attr.read_text(encoding="utf-8")).get("tournament_name")
        except (ValueError, OSError):
            continue
        if n:
            names.add(n)
    return names


def _event_series(events_root: Path) -> set[str]:
    out = set()
    for d in events_root.rglob("derived.json"):
        try:
            s = (json.loads(d.read_text(encoding="utf-8")).get("naming") or {}).get("series_event")
        except (ValueError, OSError):
            continue
        if s is not None:
            out.add(s)
    return out


def _countries(region_dir: Path) -> set[str]:
    out = set()
    uj = region_dir / "users.jsonl"
    if uj.exists():
        for line in uj.open(encoding="utf-8"):
            try:
                c = json.loads(line).get("country")
            except ValueError:
                continue
            if c:
                out.add(c)
    return out


def build_rules(clf, naming, country, region_dir: Path, events_root: Path) -> dict:
    names = {}
    for n in sorted(_tournament_names(region_dir, events_root)):
        names[n] = {
            "series": naming.tournament_series(n),
            "series_number": naming.tournament_series_number(n),
            "award_label": naming.tournament_award_label(n),
            "individual_label": naming.tournament_individual_label(n),
            "cancelled": bool(naming.CANCELLED_PATTERN.search(n)),
            "test_page": bool(naming.TEST_PATTERN.search(n)),
        }
    series = {}
    for s in sorted(_event_series(events_root)):
        community = naming.community_series(s)
        series[s] = {
            "community": community,
            "non_serious": bool(clf.NON_SERIOUS_PATTERN.search(community)),
            "uchi": bool(clf.UCHI_PATTERN.search(community)),
            "special_rules": bool(clf.SPECIAL_RULES_PATTERN.search(community)),
            "restricted": bool(clf.RESTRICTED_PATTERN.search(community)),
        }
    return {
        "version": VERSION,
        "constants": {
            "TIMEZONE": clf.TIMEZONE,
            "CLASSIFIER_VERSION": clf.CLASSIFIER_VERSION,
            "SMACOMI_FORCE_WEEKDAY_MAX_NENT": clf.SMACOMI_FORCE_WEEKDAY_MAX_NENT,
            "FORCE_WEEKEND_MIN_NENT": clf.FORCE_WEEKEND_MIN_NENT,
        },
        "country_codes": sorted(country.COUNTRY_CODES),
        "countries": {c: {"ja": country.country_ja(c)} for c in sorted(_countries(region_dir))},
        "tournament_names": names,
        "series": series,
    }


def write_region_rules(clf, naming, country, region_dir: Path, events_root: Path, dry_run: bool = False):
    """(written, rules). Writes only when the content changed."""
    rules = build_rules(clf, naming, country, region_dir, events_root)
    text = json.dumps(rules, ensure_ascii=False, sort_keys=False, separators=(",", ":")) + "\n"
    out = region_dir / FILE
    if out.exists() and out.read_text(encoding="utf-8") == text:
        return False, rules
    if not dry_run:
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
    return True, rules
