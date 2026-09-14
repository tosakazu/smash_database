# -*- coding: utf-8 -*-
"""Japan: the catalogue of geographic units (都道府県) that derive.py writes to data/startgg/Japan/geo.json.

This is the single place that says what the units are, how they are named in each language, in what order they
are listed, and how they are grouped. The build copies geo.json into the site as-is (site/data/geo.json), so the
ranking build and the front end never carry their own list of prefectures.

Unit ids are the values that classify.py writes: `place.geo` in derived.json and `geo` in users_derived.jsonl
(the full Japanese name, 北海道 / 東京都 / 大阪府 / 神奈川県 …). Changing an id changes the data contract, so
bump CLASSIFIER_VERSION in classify.py and re-run derive.py --all when you do.

Contract of the returned catalogue (the same for every region; see docs/region_operator.md, section 7):
  region       the region directory name
  unit         what one unit is (prefecture / state), as an identifier
  provisional  True while the rules are a stopgap (North America), False when they are the real thing
  names        {lang: label} for the unit kind (都道府県 / Prefecture)
  units        ordered list of {id, order, name: {lang: …}, group}. `order` is the listing order (JIS code here)
  groups       exhaustive partition of the units: [{id, name: {lang: …}, units: [ids]}] (地方)
  seed_groups  optional merges used by the seeding tool's collision avoidance: [{id, name, units, default}]
"""
from __future__ import annotations

# (id = full name, JIS code, romanised English name, 地方)
_PREFS = [
    ('北海道', 1, 'Hokkaido', 'hokkaido'),
    ('青森県', 2, 'Aomori', 'tohoku'), ('岩手県', 3, 'Iwate', 'tohoku'), ('宮城県', 4, 'Miyagi', 'tohoku'),
    ('秋田県', 5, 'Akita', 'tohoku'), ('山形県', 6, 'Yamagata', 'tohoku'), ('福島県', 7, 'Fukushima', 'tohoku'),
    ('茨城県', 8, 'Ibaraki', 'kanto'), ('栃木県', 9, 'Tochigi', 'kanto'), ('群馬県', 10, 'Gunma', 'kanto'),
    ('埼玉県', 11, 'Saitama', 'kanto'), ('千葉県', 12, 'Chiba', 'kanto'), ('東京都', 13, 'Tokyo', 'kanto'),
    ('神奈川県', 14, 'Kanagawa', 'kanto'),
    ('新潟県', 15, 'Niigata', 'chubu'), ('富山県', 16, 'Toyama', 'chubu'), ('石川県', 17, 'Ishikawa', 'chubu'),
    ('福井県', 18, 'Fukui', 'chubu'), ('山梨県', 19, 'Yamanashi', 'chubu'), ('長野県', 20, 'Nagano', 'chubu'),
    ('岐阜県', 21, 'Gifu', 'chubu'), ('静岡県', 22, 'Shizuoka', 'chubu'), ('愛知県', 23, 'Aichi', 'chubu'),
    ('三重県', 24, 'Mie', 'kinki'), ('滋賀県', 25, 'Shiga', 'kinki'), ('京都府', 26, 'Kyoto', 'kinki'),
    ('大阪府', 27, 'Osaka', 'kinki'), ('兵庫県', 28, 'Hyogo', 'kinki'), ('奈良県', 29, 'Nara', 'kinki'),
    ('和歌山県', 30, 'Wakayama', 'kinki'),
    ('鳥取県', 31, 'Tottori', 'chugoku'), ('島根県', 32, 'Shimane', 'chugoku'), ('岡山県', 33, 'Okayama', 'chugoku'),
    ('広島県', 34, 'Hiroshima', 'chugoku'), ('山口県', 35, 'Yamaguchi', 'chugoku'),
    ('徳島県', 36, 'Tokushima', 'shikoku'), ('香川県', 37, 'Kagawa', 'shikoku'), ('愛媛県', 38, 'Ehime', 'shikoku'),
    ('高知県', 39, 'Kochi', 'shikoku'),
    ('福岡県', 40, 'Fukuoka', 'kyushu'), ('佐賀県', 41, 'Saga', 'kyushu'), ('長崎県', 42, 'Nagasaki', 'kyushu'),
    ('熊本県', 43, 'Kumamoto', 'kyushu'), ('大分県', 44, 'Oita', 'kyushu'), ('宮崎県', 45, 'Miyazaki', 'kyushu'),
    ('鹿児島県', 46, 'Kagoshima', 'kyushu'), ('沖縄県', 47, 'Okinawa', 'kyushu'),
]
_GROUPS = [
    ('hokkaido', {'ja': '北海道', 'en': 'Hokkaido'}),
    ('tohoku', {'ja': '東北', 'en': 'Tohoku'}),
    ('kanto', {'ja': '関東', 'en': 'Kanto'}),
    ('chubu', {'ja': '中部', 'en': 'Chubu'}),
    ('kinki', {'ja': '近畿', 'en': 'Kinki'}),
    ('chugoku', {'ja': '中国', 'en': 'Chugoku'}),
    ('shikoku', {'ja': '四国', 'en': 'Shikoku'}),
    ('kyushu', {'ja': '九州・沖縄', 'en': 'Kyushu & Okinawa'}),
]
# Merges the seeding tool offers as toggles ("treat these as one region" for collision avoidance).
# `default` is the toggle's initial state (both ON since 2026-09-14).
_SEED_GROUPS = [
    ('minamiKanto', {'ja': '南関東', 'en': 'South Kanto'}, ['埼玉県', '千葉県', '東京都', '神奈川県'], True),
    ('keihanshin', {'ja': '京阪神', 'en': 'Keihanshin'}, ['京都府', '大阪府', '兵庫県'], True),
]

UNIT_IDS = [p[0] for p in _PREFS]   # ordered; classify.py uses it as PREFECTURES


def catalog() -> dict:
    """The geo.json content for Japan."""
    return {
        'region': 'Japan',
        'unit': 'prefecture',
        'provisional': False,
        'names': {'ja': '都道府県', 'en': 'Prefecture'},
        'units': [{'id': pid, 'order': code, 'name': {'ja': pid, 'en': en}, 'group': grp}
                  for pid, code, en, grp in _PREFS],
        'groups': [{'id': gid, 'name': name, 'units': [p[0] for p in _PREFS if p[3] == gid]}
                   for gid, name in _GROUPS],
        'seed_groups': [{'id': sid, 'name': name, 'units': list(units), 'default': default}
                        for sid, name, units, default in _SEED_GROUPS],
    }
