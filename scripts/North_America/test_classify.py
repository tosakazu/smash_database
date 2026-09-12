"""Tests for the North America classification rules. This module lives on the data-North_America branch and
is not on main (each region's rules are owned by that region's operator). Run from the repository root:

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
    """The initial North American rules (1on1 check and calendar only). Also verifies that no Japan-specific rules leak in."""

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
        # without a timezone in place, the region default is used
        default = na.calendar_flags(ts, None, None)
        self.assertEqual(default["timezone"], na.TIMEZONE)

    def test_holidays_are_per_country(self):
        # holidays are looked up by host country (state/provincial holidays are not included yet)
        self.assertTrue(na.is_weekend_date(dt.date(2026, 11, 26), "US"))    # Thanksgiving (4th Thu)
        self.assertFalse(na.is_weekend_date(dt.date(2026, 11, 26), "MX"))   # an ordinary weekday in Mexico
        self.assertTrue(na.is_weekend_date(dt.date(2026, 7, 1), "CA"))      # Canada Day
        self.assertFalse(na.is_weekend_date(dt.date(2026, 7, 1), "US"))
        self.assertTrue(na.is_weekend_date(dt.date(2026, 9, 16), "MX"))     # Día de la Independencia
        self.assertTrue(na.is_weekend_date(dt.date(2026, 4, 3), "CA"))      # Good Friday (2 days before Easter, 4/5)
        self.assertFalse(na.is_weekend_date(dt.date(2026, 4, 3), "US"))
        self.assertFalse(na.is_weekend_date(dt.date(2026, 11, 24), "US"))   # just an ordinary Tuesday

    def test_dominican_republic_moves_holidays_to_monday(self):
        # ley 139-97: Tue/Wed move to the preceding Monday, Thu/Fri/Sat to the following Monday
        self.assertTrue(na.is_weekend_date(dt.date(2026, 1, 5), "DO"))    # 1/6 (Tue) -> 1/5 (Mon)
        self.assertFalse(na.is_weekend_date(dt.date(2026, 1, 6), "DO"))
        self.assertTrue(na.is_weekend_date(dt.date(2026, 5, 4), "DO"))    # 5/1 (Fri) -> 5/4 (Mon)
        self.assertTrue(na.is_weekend_date(dt.date(2026, 2, 27), "DO"))   # Independence Day is not moved
        self.assertTrue(na.is_weekend_date(dt.date(2026, 6, 4), "DO"))    # Corpus Christi (Easter + 60 days)

    def test_unknown_country_gets_no_holidays(self):
        # never apply another country's calendar to a country without a table (weekends only). The table applied is recorded in derived.json
        self.assertIsNone(na.holidays_source("BR"))
        self.assertEqual(na.holidays_for("BR", 2026), frozenset())
        self.assertFalse(na.is_weekend_date(dt.date(2026, 7, 3), "BR"))   # would be an observed holiday in the US
        ts = int(dt.datetime(2026, 7, 3, 12, tzinfo=ZoneInfo("America/New_York")).timestamp())
        self.assertIsNone(na.calendar_flags(ts, None, {"country_code": "BR"})["holidays"])
        self.assertEqual(na.calendar_flags(ts, None, {"country_code": "US"})["holidays"], "US")

    def test_mexican_transmission_day(self):
        self.assertTrue(na.is_weekend_date(dt.date(2030, 12, 1), "MX"))   # inauguration day, every 6 years
        self.assertFalse(na.is_weekend_date(dt.date(2026, 12, 1), "MX"))

    def test_holiday_on_a_weekend_shifts_to_a_weekday(self):
        # 2026-07-04 (Independence Day) is a Saturday, so the day off is observed on the preceding Friday
        self.assertTrue(na.is_weekend_date(dt.date(2026, 7, 3), "US"))
        self.assertFalse(na.is_weekend_date(dt.date(2026, 7, 3), "MX"))     # observed days only for US / CA

    def test_no_japanese_calendar_rules(self):
        # Japan treats the whole New Year period (12/26 to 1/5) as holidays, but North America has no such concept.
        # 1/2 is not a public holiday in any of these countries, so it stays a weekday.
        self.assertFalse(na.is_weekend_date(dt.date(2026, 1, 2), "US"))
        self.assertFalse(na.is_weekend_date(dt.date(2026, 8, 14), "US"))    # likewise for Obon
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
        # The most common lower-class bracket in real US data. It appears both as a phase and as a separate event
        self.assertTrue(na.is_class_phase("Redemption Bracket"))
        self.assertTrue(na.is_class_phase("Redemption"))
        self.assertEqual(na.class_letter("Redemption Bracket"), "REDEMPTION")
        self.assertEqual(na.class_virtual_event_name("Ultimate Singles", "REDEMPTION"),
                         "Ultimate Singles / Redemption")
        self.assertTrue(na.name_flags("Weekly #5", "Ultimate Redemption")["lower_class"])
        self.assertTrue(na.name_flags("Novice Knockout", "Arcadian Bracket")["lower_class"])   # the tournament name is checked too
        self.assertFalse(na.name_flags("Weekly #5", "Ultimate Singles")["lower_class"])
        # appended at the end, so the existing numbering is unchanged
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
        # no start date means it is treated as a weekday
        self.assertFalse(na.upcoming_flags("X", "Singles", 0, None)["is_weekend"])

    def test_arcadian_is_restricted_not_excluded(self):
        # Arcadian = a tournament that players on the PR may not enter. As with Japan's restricted tournaments, it is aggregated but flagged
        self.assertEqual(na.is_1on1_event({"tournament_name": "Arcadian Bracket", "event_name": "Ultimate Singles"}),
                         (True, None))
        flags = na.name_flags("Arcadian Bracket", "Ultimate Singles")
        self.assertTrue(flags["restricted_tname"])
        self.assertFalse(flags["restricted_ename"])
        self.assertTrue(na.name_flags("Weekly #5", "Arcadian Singles")["restricted_ename"])
        self.assertFalse(na.name_flags("Genesis 9", "Ultimate Singles")["restricted_tname"])


if __name__ == "__main__":
    unittest.main()
