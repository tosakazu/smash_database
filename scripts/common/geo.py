"""geo.json — the catalogue of a region's geographic units (prefectures / states), written by derive.py.

The region module scripts/<Region>/geo.py declares it (`catalog()`), this file checks the contract and writes
data/startgg/<Region>/geo.json. Consumers (the ranking build, the site) read the file and never carry their own
list of units. Contract (docs/region_operator.md, section 7):

  region, unit, provisional, names{lang: label}
  units:       ordered [{id, order, name{lang}, group}]      id = the value classify.py writes into place.geo / users_derived geo
  groups:      exhaustive partition [{id, name{lang}, units[]}]
  seed_groups: optional merges [{id, name{lang}, units[], default}]
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

GEO_FILE = "geo.json"


def load_region_geo(region: str):
    """scripts/<Region>/geo.py (raises ImportError if the region has no catalogue: every region must declare one)."""
    return importlib.import_module(f"scripts.{region.replace(' ', '_')}.geo")


def validate_catalog(cat: dict) -> list[str]:
    """Contract violations as messages (empty = fine)."""
    errs = []
    for key in ("region", "unit", "provisional", "names", "units", "groups", "seed_groups"):
        if key not in cat:
            errs.append(f"missing key: {key}")
    if errs:
        return errs
    if not isinstance(cat["provisional"], bool):
        errs.append("provisional must be a bool")
    langs = set(cat["names"]) if isinstance(cat["names"], dict) else set()
    if not langs:
        errs.append("names must map at least one language to a label")
    ids = []
    for u in cat["units"]:
        for key in ("id", "order", "name", "group"):
            if key not in u:
                errs.append(f"unit {u.get('id')!r}: missing {key}")
        ids.append(u.get("id"))
        if isinstance(u.get("name"), dict) and set(u["name"]) != langs:
            errs.append(f"unit {u.get('id')!r}: name languages {sorted(u['name'])} != {sorted(langs)}")
    if len(set(ids)) != len(ids):
        errs.append("unit ids are not unique")
    if any(i in (None, "") for i in ids):
        errs.append("a unit has an empty id")
    orders = [u.get("order") for u in cat["units"]]
    if orders != sorted(orders):
        errs.append("units are not listed in `order` order")
    idset = set(ids)
    seen = set()
    gids = [g.get("id") for g in cat["groups"]]
    if len(set(gids)) != len(gids):
        errs.append("group ids are not unique")
    for g in cat["groups"]:
        for uid in g.get("units", []):
            if uid not in idset:
                errs.append(f"group {g.get('id')!r}: unknown unit {uid!r}")
            if uid in seen:
                errs.append(f"unit {uid!r} is in two groups")
            seen.add(uid)
        if isinstance(g.get("name"), dict) and set(g["name"]) != langs:
            errs.append(f"group {g.get('id')!r}: name languages differ from names")
    if seen != idset:
        errs.append(f"groups do not cover every unit (missing {sorted(idset - seen)[:5]} …)" if idset - seen
                    else "groups list units that do not exist")
    for u in cat["units"]:
        if u.get("group") not in set(gids):
            errs.append(f"unit {u.get('id')!r}: group {u.get('group')!r} is not a group id")
    for sg in cat["seed_groups"]:
        for key in ("id", "name", "units", "default"):
            if key not in sg:
                errs.append(f"seed group {sg.get('id')!r}: missing {key}")
        for uid in sg.get("units", []):
            if uid not in idset:
                errs.append(f"seed group {sg.get('id')!r}: unknown unit {uid!r}")
        if not isinstance(sg.get("default"), bool):
            errs.append(f"seed group {sg.get('id')!r}: default must be a bool")
    return errs


def write_geo_json(region: str, region_dir: Path, dry_run: bool = False) -> tuple[bool, dict]:
    """Write <region_dir>/geo.json from the region module. Returns (written, catalogue). Raises SystemExit on a contract violation."""
    cat = load_region_geo(region).catalog()
    errs = validate_catalog(cat)
    if errs:
        raise SystemExit("ERROR: scripts/%s/geo.py catalog() breaks the geo.json contract:\n  " % region + "\n  ".join(errs))
    text = json.dumps(cat, ensure_ascii=False, indent=1) + "\n"
    out = Path(region_dir) / GEO_FILE
    if out.exists() and out.read_text(encoding="utf-8") == text:
        return False, cat
    if not dry_run:
        tmp = out.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(out)
    return True, cat
