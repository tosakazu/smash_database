# -*- coding: utf-8 -*-
"""Load the region module (scripts/<region>/classify.py) and check the features it declares.

The classification itself belongs to the region; the common scripts borrow it through here. Nothing missing
is guessed (no default substitutes) — so that adding a region never silently applies Japan's rules.
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace


def load_region_classifier(region: str):
    """scripts/<region>/classify.py (has CLASSIFIER_VERSION and classify_event). Stop if missing."""
    modname = f"scripts.{region.replace(' ', '_')}.classify"
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        raise SystemExit(f"classifier module {modname} for region {region!r} not found: {e}")


# ── class brackets (separate lower-tier brackets held inside a tournament) ──
# Japan's B/C/D/E classes are the typical case, but names and existence vary by region (e.g. Amateur in NA).
# A region module is treated as supporting class brackets if it declares:
#   CLASS_LETTERS                     class identifiers (their order is used to number virtual event IDs)
#   is_class_phase(name)              whether a phase name is a class bracket (is_class in phases.json)
#   is_unseparated_class_phase(name)  find unseparated class matches from phase names in matches.json
#   class_letter(name)                phase name → class identifier (None if not one)
#   class_virtual_event_name(ev, l)   event name used when a class is split out as its own tournament
CLASS_HOOKS = ("CLASS_LETTERS", "is_class_phase", "is_unseparated_class_phase",
               "class_letter", "class_virtual_event_name")


def class_support(clf) -> SimpleNamespace | None:
    """The hooks if the region handles class brackets, else None. A partial declaration stops the run."""
    present = [name for name in CLASS_HOOKS if getattr(clf, name, None) is not None]
    if not present:
        return None
    missing = [name for name in CLASS_HOOKS if name not in present]
    if missing:
        raise SystemExit(f"{clf.__name__}: class bracket hooks missing ({', '.join(missing)})")
    return SimpleNamespace(**{name: getattr(clf, name) for name in CLASS_HOOKS})


def class_phase_group_ids(phases_data: dict | None, class_phase_files: list[dict]) -> tuple[bool, list[int]]:
    """(are all phases class brackets, list of class-bracket phase_group_ids). Region-independent (is_class is set by the region).
    An event whose phases are all is_class is itself a class tournament, so its matches count as the main bracket (= empty list)."""
    if not phases_data:
        return False, []
    phs = phases_data.get("phases") or []
    if phs and all(p.get("is_class") for p in phs):
        return True, []
    ids: set[int] = set()
    for cp in class_phase_files:
        for pg in (cp.get("phase_groups") or []):
            pgid = pg.get("phase_group_id")
            if pgid is not None:
                try:
                    ids.add(int(pgid))
                except (ValueError, TypeError):
                    pass
    return False, sorted(ids)
