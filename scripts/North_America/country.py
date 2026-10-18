# -*- coding: utf-8 -*-
"""North America: which countries belong to the region, and how a country is displayed.

- COUNTRY_CODES: start.gg country codes of venues that belong to this region (attr.place.country_code).
  Events elsewhere are not part of the region's tournament index.
- is_overseas_country: a player's registered country (users.jsonl `country`, a full English name) is outside
  the region → the player gets the "overseas" tag (does not consume a place in region-only counters).
  Unregistered (None / "") is never judged.
- country_ja: display name of a country. The North American site is in English, so the start.gg name is
  returned as-is (the function name is the contract's; the language is the region's choice).

Scope (2026-10-18): the whole North American continent, as the UN M49 geoscheme defines it — Northern
America + Central America + Caribbean — plus Guam, which predates this list. Guam is Oceania, not North
America; it is kept because it was already in scope and dropping it would retire data we hold (51 events).
The previous list was US/CA/MX/PR/VI/GU/DO only.

Three lists have to agree for a country to reach the ranking, and they are owned by different people:
  1. scripts/common/utils.py country_code2region   routes a downloaded country into this region's directory
                                                   and branch. `main`'s file, not the region operator's.
  2. COUNTRY_CODES below                           what the ranking build treats as in-region (via
                                                   region_rules.json country_codes).
  3. the download driver's country loop            backfill_north_america.sh / run_region.sh --country-code.
A country missing from (1) lands in data/startgg/Other instead; missing from (3) is simply never fetched.
"""
from __future__ import annotations

# Northern America (UN M49). US and CA are the bulk of the region; the rest are small but real scenes.
_NORTHERN_AMERICA = {'US', 'CA', 'GL', 'BM', 'PM'}
# Central America (UN M49). Mexico belongs here in the geoscheme, not to Northern America.
_CENTRAL_AMERICA = {'MX', 'BZ', 'CR', 'SV', 'GT', 'HN', 'NI', 'PA'}
# Caribbean (UN M49).
_CARIBBEAN = {
    'AI', 'AG', 'AW', 'BS', 'BB', 'BQ', 'VG', 'KY', 'CU', 'CW', 'DM', 'DO', 'GD', 'GP',
    'HT', 'JM', 'MQ', 'MS', 'PR', 'BL', 'KN', 'LC', 'MF', 'VC', 'SX', 'TC', 'TT', 'VI',
}
# Pre-existing, kept for continuity (Oceania, not North America) — see the scope note above.
_LEGACY = {'GU'}

COUNTRY_CODES = frozenset(_NORTHERN_AMERICA | _CENTRAL_AMERICA | _CARIBBEAN | _LEGACY)

# Registered-country names as start.gg spells them. Every name below that a player has actually used was
# taken from users.jsonl (234,646 rows, 2026-10-18) rather than guessed — "United States Virgin Islands",
# not "U.S. Virgin Islands". For the handful of territories no registered player has yet, start.gg's exact
# spelling is unverified, so both the ISO 3166 form and the common alternative are listed; a name that
# turns out to be wrong only costs those players an "overseas" tag, never an exclusion from the index.
REGION_COUNTRIES = frozenset({
    # Northern America
    'United States', 'Canada', 'Greenland', 'Bermuda', 'Saint Pierre and Miquelon',
    # Central America
    'Mexico', 'Belize', 'Costa Rica', 'El Salvador', 'Guatemala', 'Honduras', 'Nicaragua', 'Panama',
    # Caribbean
    'Anguilla', 'Antigua and Barbuda', 'Aruba', 'Bahamas', 'Barbados', 'British Virgin Islands',
    'Cayman Islands', 'Cuba', 'Dominica', 'Dominican Republic', 'Grenada', 'Guadeloupe', 'Haiti',
    'Jamaica', 'Martinique', 'Montserrat', 'Puerto Rico', 'Saint Kitts and Nevis', 'Saint Lucia',
    'Saint Vincent and the Grenadines', 'Trinidad and Tobago', 'Turks and Caicos Islands',
    'United States Virgin Islands',
    # Caribbean, spelling unverified (no registered player yet) — ISO form and the common alternative
    'Curaçao', 'Curacao',
    'Bonaire, Sint Eustatius and Saba', 'Caribbean Netherlands',
    'Saint Barthélemy', 'Saint Barthelemy',
    'Saint Martin', 'Saint Martin (French part)',
    'Sint Maarten', 'Sint Maarten (Dutch part)',
    # Pre-existing (see scope note)
    'Guam',
})


def country_ja(name: str | None) -> str | None:
    """Display name of a country (English for this region; None stays None)."""
    return name or None


def is_overseas_country(country: str | None) -> bool:
    """Registered country is outside North America (unregistered → False = not judged)."""
    return bool(country) and country not in REGION_COUNTRIES
