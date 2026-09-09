"""判定ルール (scripts/Japan/*.py) の代表例テスト。

derive.py が書く derived.json / users_derived.jsonl の中身はここのルールで決まり、
ランキング (spsp) はそれを読むだけなので、ルールを直したときに意図しない判定変化が
起きていないかをここで押さえる。網羅ではなく「この挙動は仕様」と言える例だけを置く。
実データ全件の突き合わせは spsp 側の golden ビルド比較が担当。
"""

import datetime as dt
import os
import pathlib
import time
import unittest
from zoneinfo import ZoneInfo

os.environ.setdefault("TZ", "Asia/Tokyo")
time.tzset()

from scripts.Japan import classify, country, naming, prefecture
from scripts.North_America import classify as na


def jst(text: str) -> int:
    """'2026-09-05 12:00' (JST) → epoch 秒。"""
    return int(dt.datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp())


def attr(tname: str, ename: str = "シングルス") -> dict:
    return {"tournament_name": tname, "event_name": ename}


class Is1on1Tests(unittest.TestCase):
    """1on1 として集計するイベントか (名前だけで判定する)。"""

    def test_singles_is_1on1(self):
        for tname in ("篝火#10", "ウメブラSP8", "スマバト SP #50"):
            with self.subTest(tname=tname):
                self.assertEqual(classify.is_1on1_event(attr(tname)), (True, None))

    def test_doubles_and_team_are_excluded(self):
        for tname, reason in (
            ("第1回 2on2 チーム大会", "keyword:チーム"),
            ("スマッシュサバイバル", "keyword:スマッシュサバイバル"),
            ("amiibo大会", "keyword:amiibo"),
        ):
            with self.subTest(tname=tname):
                self.assertEqual(classify.is_1on1_event(attr(tname)), (False, reason))

    def test_nvn_regex_excludes_doubles_notation(self):
        ok, why = classify.is_1on1_event(attr("スマブラ大会", "3vs3"))
        self.assertFalse(ok)
        self.assertEqual(why, "regex:NvN")

    def test_allow_pattern_wins_over_exclude(self):
        # 除外語を含んでいても ALLOW_PATTERNS に載っていれば 1on1 として扱う
        self.assertTrue(classify.ALLOW_PATTERNS, "ALLOW_PATTERNS が空になっている")
        allow = classify.ALLOW_PATTERNS[0]
        self.assertEqual(classify.is_1on1_event(attr(f"{allow} チーム")), (True, None))


class NameFlagTests(unittest.TestCase):
    """大会名から決まるフラグ。restricted は「参加条件で母集団が絞られる大会」。"""

    def test_restricted_by_rate_limit(self):
        for tname in ("風雲 #1 1700未満制限大会", "R1700以下限定大会", "レート1500以上限定"):
            with self.subTest(tname=tname):
                self.assertTrue(classify.name_flags(tname, "シングルス")["restricted_tname"])

    def test_qualifier_rate_format_is_not_restricted(self):
        # 「予選レート形式」は進行方式の話で、参加できる層は絞られない
        self.assertFalse(classify.name_flags("予選レート形式 スマブラ大会", "シングルス")["restricted_tname"])

    def test_lower_class_and_pre(self):
        self.assertTrue(classify.name_flags("Bクラス限定", "シングルス")["lower_class"])
        self.assertTrue(classify.name_flags("プレ大会 スマバト", "シングルス")["pre"])

    def test_event_name_is_checked_separately_for_restricted(self):
        flags = classify.name_flags("スマブラ大会", "1700未満制限")
        self.assertFalse(flags["restricted_tname"])
        self.assertTrue(flags["restricted_ename"])

    def test_smacomi_is_weekday_only_for_small_editions(self):
        # 上野スマコミは規模で決まるので、名前だけの force_weekday には入れない。
        # derived.json には smacomi フラグを書き、参加者数との合成は読む側 (spsp) が行う。
        flags = classify.name_flags("上野スマコミ #48", "シングルス")
        self.assertTrue(flags["smacomi"])
        self.assertFalse(flags["force_weekday"])
        limit = classify.SMACOMI_FORCE_WEEKDAY_MAX_NENT
        self.assertTrue(classify.is_force_weekday_tournament("上野スマコミ #48", "シングルス", limit - 1))
        self.assertFalse(classify.is_force_weekday_tournament("上野スマコミ #48", "シングルス", limit))
        # 名前で決まるシリーズは参加者数によらず平日扱い
        self.assertTrue(classify.is_force_weekday_tournament("大菊月 #7", "シングルス", 500))

    def test_force_weekday_series(self):
        self.assertTrue(classify.name_flags("大菊月 #7", "シングルス")["force_weekday"])
        self.assertTrue(classify.name_flags("渋谷BeeSmash #12", "シングルス")["force_weekday"])
        # BIG 回と上野開催は通常の週末大会として扱う
        self.assertFalse(classify.name_flags("渋谷BeeSmash BIG #3", "シングルス")["force_weekday"])


class CalendarTests(unittest.TestCase):
    def test_weekend_and_weekday(self):
        self.assertTrue(classify.calendar_flags(jst("2026-09-05 12:00"), None)["is_weekend_real"])
        self.assertFalse(classify.calendar_flags(jst("2026-09-07 19:00"), None)["is_weekend_real"])

    def test_new_year_is_force_weekend(self):
        flags = classify.calendar_flags(jst("2026-01-02 12:00"), None)
        self.assertFalse(flags["is_weekend_real"])
        self.assertTrue(flags["is_force_weekend_period"])

    def test_range_counts_as_weekend_if_it_covers_one(self):
        flags = classify.calendar_flags(jst("2026-09-04 20:00"), jst("2026-09-06 20:00"))
        self.assertEqual((flags["date"], flags["end_date"]), ("2026-09-04", "2026-09-06"))
        self.assertTrue(flags["is_weekend_real"])


class PlaceTests(unittest.TestCase):
    def test_address_wins_over_city(self):
        pref = classify.place_prefecture(
            {"country_code": "JP", "city": "北名古屋市", "venue_address": "愛知県北名古屋市徳重..."}
        )
        self.assertEqual(pref, "愛知県")

    def test_city_only(self):
        self.assertEqual(
            classify.place_prefecture({"country_code": "JP", "city": "Shibuya", "venue_address": None}),
            "東京都",
        )

    def test_outside_japan_is_none(self):
        self.assertIsNone(classify.place_prefecture({"country_code": "US", "city": "Los Angeles"}))
        self.assertIsNone(classify.place_prefecture(None))

    def test_resolver_sources(self):
        self.assertEqual(prefecture.resolve("大阪市北区")[0], "大阪府")
        self.assertEqual(prefecture.resolve("横浜市")[0], "神奈川県")
        self.assertEqual(prefecture.resolve("Sapporo")[0], "北海道")
        self.assertEqual(prefecture.resolve("???"), (None, "unmatched"))


class NamingTests(unittest.TestCase):
    def test_series_and_number(self):
        for tname, series, number in (
            ("篝火#10", "篝火", 10),
            ("スマパ！#123", "スマパ", 123),
            ("ウメブラSP8", "ウメブラ", 8),
            ("第3回 マエスマTOP", "マエスマTOP", 3),
        ):
            with self.subTest(tname=tname):
                self.assertEqual(naming.tournament_series(tname), series)
                self.assertEqual(naming.tournament_series_number(tname), number)

    def test_award_label_drops_the_number_individual_keeps_it(self):
        self.assertEqual(naming.tournament_award_label("篝火#10"), "篝火")
        self.assertEqual(naming.tournament_individual_label("篝火#10"), "篝火#10")

    def test_cancelled_and_test_page(self):
        self.assertTrue(naming.is_cancelled("【中止】スマバト SP #50"))
        self.assertFalse(naming.is_cancelled("スマバト SP #50"))
        self.assertTrue(naming.is_test_page("テスト大会"))

    def test_naming_labels_shape(self):
        labels = naming.naming_labels("篝火#10", "シングルス")
        self.assertEqual(
            labels,
            {
                "series": "篝火",
                "series_number": 10,
                "award_label": "篝火",
                "individual_label": "篝火#10",
                "cancelled": False,
                "test_page": False,
            },
        )

    def test_community_series_merges_sibling_series(self):
        self.assertEqual(naming.community_series("スマパ 拡大版"), "スマパ")
        self.assertEqual(naming.community_series("彩Trial"), "彩")
        # 集約対象でないシリーズはそのまま
        self.assertEqual(naming.community_series("篝火"), "篝火")


class CountryTests(unittest.TestCase):
    def test_country_ja(self):
        self.assertEqual(country.country_ja("Japan"), "日本")
        self.assertEqual(country.country_ja("United States"), "アメリカ")
        self.assertIsNone(country.country_ja(None))

    def test_unknown_country_falls_back_to_the_english_name(self):
        self.assertEqual(country.country_ja("Neverland"), "Neverland")

    def test_is_overseas_country(self):
        self.assertFalse(country.is_overseas_country("Japan"))
        self.assertTrue(country.is_overseas_country("Korea, Republic of"))
        # 未登録は「海外と判断しない」(手動表 overseas_manual.json が拾う)
        self.assertFalse(country.is_overseas_country(None))
        self.assertFalse(country.is_overseas_country(""))


class ClassifyUserTests(unittest.TestCase):
    def test_japanese_user_with_city(self):
        got = classify.classify_user({"user_id": 1, "country": "Japan", "city": "横浜市"})
        self.assertEqual(got["prefecture"], "神奈川県")

    def test_user_without_city_or_outside_japan_is_skipped(self):
        self.assertIsNone(classify.classify_user({"user_id": 1, "country": "Japan", "city": None}))
        self.assertIsNone(classify.classify_user({"user_id": 2, "country": "France", "city": "Paris"}))


class RegionModuleContractTests(unittest.TestCase):
    """derive.py が地域モジュールに求めるもの。地域を足したらここが落ちて気づける。"""

    def _region_modules(self):
        import importlib
        root = pathlib.Path(__file__).resolve().parents[1]
        for d in sorted(root.iterdir()):
            if d.is_dir() and (d / "classify.py").exists() and d.name not in ("common", "test"):
                yield d.name, importlib.import_module(f"scripts.{d.name}.classify")

    def test_every_region_declares_the_contract(self):
        found = 0
        for name, mod in self._region_modules():
            found += 1
            with self.subTest(region=name):
                self.assertIsInstance(mod.CLASSIFIER_VERSION, int)
                # 暦を決めるタイムゾーン。日本の暦で他地域を判定しないための必須項目
                self.assertTrue(getattr(mod, "TIMEZONE", None), f"{name}: TIMEZONE が要る")
                ZoneInfo(mod.TIMEZONE)      # 実在するタイムゾーン名か
                self.assertTrue(callable(mod.classify_event))
                self.assertTrue(callable(mod.classify_user))
        self.assertGreaterEqual(found, 2, "Japan と North_America が見えるはず")


class NorthAmericaTests(unittest.TestCase):
    """北米の初版ルール (1on1 判定と暦だけ)。日本固有の判定を持ち込まないことも確かめる。"""

    def test_singles_and_excluded_formats(self):
        self.assertEqual(na.is_1on1_event({"event_name": "Ultimate Singles", "tournament_name": "Genesis 9"}),
                         (True, None))
        for ename, reason in (("Ultimate Doubles", "keyword:doubles"),
                              ("2v2 Crew Battle", "keyword:2v2"),
                              ("Squad Strike", "keyword:squad strike"),
                              ("Arcadian Bracket", "keyword:arcadian"),
                              ("Dobles Amistosos", "keyword:dobles")):
            with self.subTest(ename=ename):
                self.assertEqual(na.is_1on1_event({"event_name": ename, "tournament_name": "X"}),
                                 (False, reason))
        ok, why = na.is_1on1_event({"event_name": "3 vs 3 Invitational", "tournament_name": "X"})
        self.assertEqual((ok, why), (False, "regex:NvN"))

    def test_calendar_uses_the_events_own_timezone(self):
        ts = 1788944400   # 2026-09-10 09:00 JST = 2026-09-09 17:00 PT
        west = na.calendar_flags(ts, None, {"timezone": "America/Los_Angeles"})
        self.assertEqual((west["date"], west["timezone"]), ("2026-09-09", "America/Los_Angeles"))
        # place に timezone が無ければ地域の既定
        default = na.calendar_flags(ts, None, None)
        self.assertEqual(default["timezone"], na.TIMEZONE)

    def test_no_japanese_calendar_rules(self):
        # 正月 (1/2) は日本では休日扱いだが、北米で休日とするかは未定なので週末にしない
        newyear = dt.date(2026, 1, 2)
        self.assertFalse(na.is_weekend_date(newyear))
        self.assertNotIn("is_force_weekend_period",
                         na.calendar_flags(int(dt.datetime(2026, 1, 2, 12).timestamp()), None, None))
        self.assertEqual(na.HOLIDAY_DATES, frozenset())   # 祝日は運用担当者が入れる

    def test_no_user_derivation_yet(self):
        self.assertIsNone(na.classify_user({"user_id": 1, "country": "United States", "city": "Seattle"}))


if __name__ == "__main__":
    unittest.main()
