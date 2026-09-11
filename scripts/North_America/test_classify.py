"""North America の判定ルールのテスト。このモジュールは data-North_America ブランチにあり、
main には無い (地域の規則はその地域の担当者が持つ)。実行はリポジトリのルートで:

    python3 -m unittest scripts.North_America.test_classify
"""

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

from scripts.North_America import classify as na
from scripts.common.region import class_support


class ContractTests(unittest.TestCase):
    def test_declares_the_region_contract(self):
        self.assertIsInstance(na.CLASSIFIER_VERSION, int)
        ZoneInfo(na.TIMEZONE)
        self.assertTrue(callable(na.classify_event) and callable(na.classify_user))
        self.assertIsNotNone(class_support(na))


class RulesTests(unittest.TestCase):
    """北米の初版ルール (1on1 判定と暦だけ)。日本固有の判定を持ち込まないことも確かめる。"""

    def test_singles_and_excluded_formats(self):
        self.assertEqual(na.is_1on1_event({"event_name": "Ultimate Singles", "tournament_name": "Genesis 9"}),
                         (True, None))
        for ename, reason in (("Ultimate Doubles", "keyword:doubles"),
                              ("2v2 Crew Battle", "keyword:2v2"),
                              ("Squad Strike", "keyword:squad strike"),
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



class ClassBracketTests(unittest.TestCase):

    def test_north_america_redemption_is_a_class_bracket(self):
        # 米国の実データで最も多い下位ブラケット。phase としても別イベントとしても現れる
        self.assertTrue(na.is_class_phase("Redemption Bracket"))
        self.assertTrue(na.is_class_phase("Redemption"))
        self.assertEqual(na.class_letter("Redemption Bracket"), "REDEMPTION")
        self.assertEqual(na.class_virtual_event_name("Ultimate Singles", "REDEMPTION"),
                         "Ultimate Singles / Redemption")
        self.assertTrue(na.name_flags("Weekly #5", "Ultimate Redemption")["lower_class"])
        self.assertTrue(na.name_flags("Novice Knockout", "Arcadian Bracket")["lower_class"])   # 大会名でも見る
        self.assertFalse(na.name_flags("Weekly #5", "Ultimate Singles")["lower_class"])
        # 末尾に足したので既存の採番は動かない
        self.assertEqual(na.CLASS_LETTERS.index("REDEMPTION"), len(na.CLASS_LETTERS) - 1)

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


class UpcomingAndRestrictedTests(unittest.TestCase):

    def test_north_america_upcoming_is_minimal(self):
        sat = int(dt.datetime(2026, 9, 5, 12).timestamp())
        flags = na.upcoming_flags("Genesis 9", "Ultimate Singles", 500, sat)
        self.assertEqual((flags["is_1on1"], flags["is_weekend"]), (True, True))
        self.assertFalse(na.upcoming_flags("Genesis 9", "Ultimate Doubles", 500, sat)["is_1on1"])
        # 開始日が無ければ平日扱い
        self.assertFalse(na.upcoming_flags("X", "Singles", 0, None)["is_weekend"])

    def test_arcadian_is_restricted_not_excluded(self):
        # Arcadian = PR 入り選手は出られない大会。日本の制限大会と同じく、集計はするが印を付ける
        self.assertEqual(na.is_1on1_event({"tournament_name": "Arcadian Bracket", "event_name": "Ultimate Singles"}),
                         (True, None))
        flags = na.name_flags("Arcadian Bracket", "Ultimate Singles")
        self.assertTrue(flags["restricted_tname"])
        self.assertFalse(flags["restricted_ename"])
        self.assertTrue(na.name_flags("Weekly #5", "Arcadian Singles")["restricted_ename"])
        self.assertFalse(na.name_flags("Genesis 9", "Ultimate Singles")["restricted_tname"])


if __name__ == "__main__":
    unittest.main()
