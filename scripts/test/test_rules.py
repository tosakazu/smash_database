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

    def test_class_hooks_are_all_or_nothing(self):
        from scripts.common.region import class_support
        for name, mod in self._region_modules():
            with self.subTest(region=name):
                cls = class_support(mod)      # 中途半端な宣言なら SystemExit
                if cls is not None:
                    self.assertTrue(cls.CLASS_LETTERS, f"{name}: CLASS_LETTERS が空")
                    for letter in cls.CLASS_LETTERS:
                        self.assertIsInstance(cls.class_virtual_event_name("Singles", letter), str)

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


class UpcomingTests(unittest.TestCase):
    """開催予定 (まだイベントのディレクトリが無い大会) に付ける判定。地域ごと。"""

    def test_japan_upcoming_matches_the_event_rules(self):
        sat = int(dt.datetime(2026, 9, 5, 12).timestamp())      # 土曜
        flags = classify.upcoming_flags("篝火#10", "シングルス", 200, sat)
        self.assertTrue(flags["is_weekend"])
        self.assertTrue(flags["is_weekend_real"])
        self.assertFalse(flags["is_restricted"])
        # 大会名の判定は derived.json と同じ定義元を使う
        self.assertTrue(classify.upcoming_flags("風雲 #1 1700未満制限大会", "シングルス", 30, sat)["is_restricted"])
        self.assertTrue(classify.upcoming_flags("プレ大会 スマバト", "シングルス", 30, sat)["is_pre"])
        # プレ大会は週末でもポイント対象外なので is_weekend を落とす (build と同じ規約)
        self.assertFalse(classify.upcoming_flags("プレ大会 スマバト", "シングルス", 30, sat)["is_weekend"])

    def test_japan_force_weekend_needs_the_size(self):
        newyear = int(dt.datetime(2026, 1, 2, 12).timestamp())   # 金曜だが年末年始
        self.assertTrue(classify.upcoming_flags("大会", "シングルス", 200, newyear)["is_weekend"])
        self.assertFalse(classify.upcoming_flags("大会", "シングルス", 20, newyear)["is_weekend"])
        self.assertFalse(classify.upcoming_flags("大会", "シングルス", 200, newyear)["is_weekend_real"])

    def test_north_america_upcoming_is_minimal(self):
        sat = int(dt.datetime(2026, 9, 5, 12).timestamp())
        flags = na.upcoming_flags("Genesis 9", "Ultimate Singles", 500, sat)
        self.assertEqual((flags["is_1on1"], flags["is_weekend"]), (True, True))
        self.assertFalse(na.upcoming_flags("Genesis 9", "Ultimate Doubles", 500, sat)["is_1on1"])
        # 開始日が無ければ平日扱い
        self.assertFalse(na.upcoming_flags("X", "Singles", 0, None)["is_weekend"])


class ClassBracketTests(unittest.TestCase):
    """大会の中の下位クラス別ブラケット。呼び名は地域ごと (日本 = B/C/D/E クラス、北米 = Amateur 等)。"""

    def test_japan_letters_and_naming(self):
        self.assertTrue(classify.is_class_phase("Bクラス"))
        self.assertTrue(classify.is_class_phase("B Class"))
        self.assertTrue(classify.is_class_phase("Ｃクラス"))       # 全角
        self.assertFalse(classify.is_class_phase("Winners Bracket"))
        self.assertEqual(classify.class_letter("Ｄクラス"), "D")
        self.assertEqual(classify.class_virtual_event_name("Singles", "B"), "Singles / Bクラス")
        # 仮想イベント ID の採番はこの並び順に依存する (変えると過去の ID がずれる)
        self.assertEqual(classify.CLASS_LETTERS, ("B", "C", "D", "E"))

    def test_japan_match_phase_pattern_has_word_boundaries(self):
        # matches.json 用のパターンは英字側に語境界がある (subclass などを拾わない)
        self.assertTrue(classify.is_unseparated_class_phase("B class"))
        self.assertFalse(classify.is_unseparated_class_phase("subclass"))

    def test_north_america_labels(self):
        self.assertTrue(na.is_class_phase("Ultimate Amateur"))
        self.assertTrue(na.is_class_phase("Novice Singles"))
        self.assertTrue(na.is_class_phase("B Class"))
        self.assertFalse(na.is_class_phase("Winners Bracket"))
        self.assertEqual(na.class_letter("Amateur Bracket"), "AMATEUR")
        self.assertEqual(na.class_letter("B Class"), "B")
        self.assertEqual(na.class_virtual_event_name("Ultimate Singles", "AMATEUR"),
                         "Ultimate Singles / Amateur")
        self.assertEqual(na.class_virtual_event_name("Ultimate Singles", "B"),
                         "Ultimate Singles / B class")

    def test_group_ids_are_region_independent(self):
        from scripts.common.region import class_phase_group_ids
        phases = {"phases": [{"is_class": False}, {"is_class": True}]}
        files = [{"phase_groups": [{"phase_group_id": 7}, {"phase_group_id": 3}]}]
        self.assertEqual(class_phase_group_ids(phases, files), (False, [3, 7]))
        # 全 phase がクラス = イベント自体がクラス大会なので、その試合は本戦扱い
        self.assertEqual(class_phase_group_ids({"phases": [{"is_class": True}]}, files), (True, []))


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

    def test_holidays_are_per_country(self):
        # 祝日は開催国で引く (州・県の祝日はまだ入れていない)
        self.assertTrue(na.is_weekend_date(dt.date(2026, 11, 26), "US"))    # Thanksgiving (4th Thu)
        self.assertFalse(na.is_weekend_date(dt.date(2026, 11, 26), "MX"))   # メキシコは平日
        self.assertTrue(na.is_weekend_date(dt.date(2026, 7, 1), "CA"))      # Canada Day
        self.assertFalse(na.is_weekend_date(dt.date(2026, 7, 1), "US"))
        self.assertTrue(na.is_weekend_date(dt.date(2026, 9, 16), "MX"))     # Día de la Independencia
        self.assertTrue(na.is_weekend_date(dt.date(2026, 4, 3), "CA"))      # Good Friday (復活祭 4/5 の 2 日前)
        self.assertFalse(na.is_weekend_date(dt.date(2026, 4, 3), "US"))
        self.assertFalse(na.is_weekend_date(dt.date(2026, 11, 24), "US"))   # ただの火曜

    def test_dominican_republic_moves_holidays_to_monday(self):
        # ley 139-97: 火・水は前の月曜、木・金・土は次の月曜
        self.assertTrue(na.is_weekend_date(dt.date(2026, 1, 5), "DO"))    # 1/6 (火) → 1/5 (月)
        self.assertFalse(na.is_weekend_date(dt.date(2026, 1, 6), "DO"))
        self.assertTrue(na.is_weekend_date(dt.date(2026, 5, 4), "DO"))    # 5/1 (金) → 5/4 (月)
        self.assertTrue(na.is_weekend_date(dt.date(2026, 2, 27), "DO"))   # 独立記念日は動かさない
        self.assertTrue(na.is_weekend_date(dt.date(2026, 6, 4), "DO"))    # Corpus Christi (復活祭 +60 日)

    def test_unknown_country_gets_no_holidays(self):
        # 表の無い国に他国の暦を当てない (土日だけ)。当てた表は derived.json に記録する
        self.assertIsNone(na.holidays_source("BR"))
        self.assertEqual(na.holidays_for("BR", 2026), frozenset())
        self.assertFalse(na.is_weekend_date(dt.date(2026, 7, 3), "BR"))   # US なら振替休日
        ts = int(dt.datetime(2026, 7, 3, 12, tzinfo=ZoneInfo("America/New_York")).timestamp())
        self.assertIsNone(na.calendar_flags(ts, None, {"country_code": "BR"})["holidays"])
        self.assertEqual(na.calendar_flags(ts, None, {"country_code": "US"})["holidays"], "US")

    def test_mexican_transmission_day(self):
        self.assertTrue(na.is_weekend_date(dt.date(2030, 12, 1), "MX"))   # 6 年ごとの就任式
        self.assertFalse(na.is_weekend_date(dt.date(2026, 12, 1), "MX"))

    def test_holiday_on_a_weekend_shifts_to_a_weekday(self):
        # 2026-07-04 (独立記念日) は土曜なので、休みは前日の金曜に振り替わる
        self.assertTrue(na.is_weekend_date(dt.date(2026, 7, 3), "US"))
        self.assertFalse(na.is_weekend_date(dt.date(2026, 7, 3), "MX"))     # 振替は US / CA だけ

    def test_no_japanese_calendar_rules(self):
        # 日本は年末年始 (12/26〜1/5) を丸ごと休日扱いするが、北米にその概念は入れていない。
        # 1/2 はどの国の祝日でもないので平日のまま。
        self.assertFalse(na.is_weekend_date(dt.date(2026, 1, 2), "US"))
        self.assertFalse(na.is_weekend_date(dt.date(2026, 8, 14), "US"))    # お盆も同じ
        self.assertNotIn("is_force_weekend_period",
                         na.calendar_flags(int(dt.datetime(2026, 1, 2, 12).timestamp()), None, None))

    def test_calendar_flags_record_the_country(self):
        ts = int(dt.datetime(2026, 11, 26, 12, tzinfo=ZoneInfo("America/New_York")).timestamp())
        flags = na.calendar_flags(ts, None, {"country_code": "US", "timezone": "America/New_York"})
        self.assertEqual(flags["date"], "2026-11-26")
        self.assertEqual(flags["country_code"], "US")
        self.assertTrue(flags["is_weekend_real"])       # Thanksgiving

    def test_no_user_derivation_yet(self):
        self.assertIsNone(na.classify_user({"user_id": 1, "country": "United States", "city": "Seattle"}))


if __name__ == "__main__":
    unittest.main()
