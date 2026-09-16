# -*- coding: utf-8 -*-
"""North America: which countries belong to the region, and how a country is displayed.

- COUNTRY_CODES: start.gg country codes of venues that belong to this region (attr.place.country_code).
  Events elsewhere are not part of the region's tournament index.
- is_overseas_country: a player's registered country (users.jsonl `country`, a full English name) is outside
  the region → the player gets the "overseas" tag (does not consume a place in region-only counters).
  Unregistered (None / "") is never judged.
- country_ja: display name of a country. The North American site is in English, so the start.gg name is
  returned as-is (the function name is the contract's; the language is the region's choice).
"""
from __future__ import annotations

COUNTRY_CODES = frozenset({'US', 'CA', 'MX', 'PR', 'VI', 'GU', 'DO'})
REGION_COUNTRIES = frozenset({
    'United States', 'Canada', 'Mexico', 'Puerto Rico', 'United States Virgin Islands', 'Guam', 'Dominican Republic',
})


def country_ja(name: str | None) -> str | None:
    """Display name of a country (English for this region; None stays None)."""
    return name or None


def is_overseas_country(country: str | None) -> bool:
    """Registered country is outside North America (unregistered → False = not judged)."""
    return bool(country) and country not in REGION_COUNTRIES
