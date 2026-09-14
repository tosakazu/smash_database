# -*- coding: utf-8 -*-
"""North America: the catalogue of geographic units that derive.py writes to data/startgg/North_America/geo.json.

PROVISIONAL (2026-09-15), like the state rules in classify.py: the unit is the US state / Canadian province
(two-letter postal code as the id, which is what classify.py writes into place.geo and users_derived.jsonl).
The operator decides the real unit (state, province, or a competitive region such as SoCal / NorCal), the
groups, and whether Mexico gets units. Until then `provisional` stays True and the site labels it as such.

Contract (the same for every region; docs/region_operator.md, section 7): region, unit, provisional, names{lang},
units[{id, order, name{lang}, group}], groups[{id, name{lang}, units[]}] (exhaustive partition),
seed_groups[{id, name{lang}, units[], default}] (merges offered by the seeding tool; none yet).
"""
from __future__ import annotations

US_STATE_NAMES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California', 'CO': 'Colorado',
    'CT': 'Connecticut', 'DE': 'Delaware', 'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii', 'ID': 'Idaho',
    'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana',
    'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi',
    'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey',
    'NM': 'New Mexico', 'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma',
    'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota',
    'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming', 'DC': 'District of Columbia',
    'PR': 'Puerto Rico', 'VI': 'U.S. Virgin Islands', 'GU': 'Guam',
}
CA_PROVINCE_NAMES = {
    'AB': 'Alberta', 'BC': 'British Columbia', 'MB': 'Manitoba', 'NB': 'New Brunswick', 'NL': 'Newfoundland and Labrador',
    'NS': 'Nova Scotia', 'NT': 'Northwest Territories', 'NU': 'Nunavut', 'ON': 'Ontario', 'PE': 'Prince Edward Island',
    'QC': 'Quebec', 'SK': 'Saskatchewan', 'YT': 'Yukon',
}
STATE_NAMES = {**US_STATE_NAMES, **CA_PROVINCE_NAMES}

_GROUPS = [
    ('us', {'en': 'United States', 'ja': 'アメリカ合衆国'}, US_STATE_NAMES),
    ('ca', {'en': 'Canada', 'ja': 'カナダ'}, CA_PROVINCE_NAMES),
]


def catalog() -> dict:
    """The geo.json content for North America (provisional)."""
    units = []
    order = 0
    for gid, _name, table in _GROUPS:
        for code, en in sorted(table.items(), key=lambda kv: kv[1]):   # alphabetical by name within a country
            order += 1
            units.append({'id': code, 'order': order, 'name': {'en': en, 'ja': en}, 'group': gid})
    return {
        'region': 'North_America',
        'unit': 'state',
        'provisional': True,
        'names': {'en': 'State / Province', 'ja': '州'},
        'units': units,
        'groups': [{'id': gid, 'name': name, 'units': sorted(table, key=lambda k: table[k])} for gid, name, table in _GROUPS],
        'seed_groups': [],
    }
