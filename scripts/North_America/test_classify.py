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
        # The key is part of the build's contract (event_contract.CALENDAR_KEYS); the rule itself does not exist here, so it is always False.
        self.assertFalse(na.calendar_flags(int(dt.datetime(2026, 1, 2, 12).timestamp()), None, None)["is_force_weekend_period"])

    def test_calendar_flags_record_the_country(self):
        ts = int(dt.datetime(2026, 11, 26, 12, tzinfo=ZoneInfo("America/New_York")).timestamp())
        flags = na.calendar_flags(ts, None, {"country_code": "US", "timezone": "America/New_York"})
        self.assertEqual(flags["date"], "2026-11-26")
        self.assertEqual(flags["country_code"], "US")
        self.assertTrue(flags["is_weekend_real"])       # Thanksgiving

    def test_user_derivation_is_provisional_only(self):
        # 2026-09-15: classify_user is no longer None, but everything it returns is marked provisional (see ProvisionalGeographyTests).
        r = na.classify_user({"user_id": 1, "country": "United States", "city": "Seattle"})
        self.assertTrue(r["provisional"])

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



class ProvisionalGeographyTests(unittest.TestCase):
    """The provisional geography (2026-09-15). Every value must say it is provisional, so the site and the operator can tell."""

    def test_venue_state_from_postal_address(self):
        self.assertEqual(na.place_state({"country_code": "US", "venue_address": "1730 E Belt Line Rd, Richardson, TX 75081, USA"}),
                         {"geo": "TX", "geo_reason": "venue_address", "provisional": True})
        self.assertEqual(na.place_state({"country_code": "US", "venue_address": "255 E Buchtel Ave, Akron, OH 44304-1234, USA"})["geo"], "OH")
        self.assertEqual(na.place_state({"country_code": "CA", "venue_address": "255 Front St W, Toronto, ON M5V 2W6, Canada"})["geo"], "ON")
        # Addresses without a postal code (a bare city entry on start.gg).
        self.assertEqual(na.place_state({"country_code": "US", "venue_address": "Bloomington, IN, USA"})["geo"], "IN")
        self.assertEqual(na.place_state({"country_code": "US", "venue_address": "Ohio, USA"})["geo"], None)

    def test_venue_state_rejects_codes_that_are_not_units_of_the_country(self):
        # A Canadian code inside a US address (or vice versa) is not a state; nothing is guessed.
        r = na.place_state({"country_code": "US", "venue_address": "1 Main St, Somewhere, ON 12345, USA"})
        self.assertEqual((r["geo"], r["geo_reason"], r["provisional"]), (None, "code_not_in_country", True))
        self.assertEqual(na.place_state({"country_code": "MX", "venue_address": "Av. Juárez 1, 06000 Ciudad de México, CDMX, Mexico"})["geo"], None)
        self.assertEqual(na.place_state(None), {"geo": None, "geo_reason": "unresolved", "provisional": True})
        self.assertEqual(na.place_state({"country_code": "US", "venue_address": ""})["geo"], None)

    def test_player_state_from_city(self):
        self.assertEqual(na.resolve_state_from_city("Chicago", "United States"), ("IL", "city_table"))
        self.assertEqual(na.resolve_state_from_city("SAN ANTONIO", "United States"), ("TX", "city_table"))
        self.assertEqual(na.resolve_state_from_city("St. Louis", "United States"), ("MO", "city_table"))
        self.assertEqual(na.resolve_state_from_city("Toronto", "Canada"), ("ON", "city_table"))
        self.assertEqual(na.resolve_state_from_city("Austin, TX", "United States"), ("TX", "city_suffix"))
        self.assertEqual(na.resolve_state_from_city("Denver CO.", "United States"), ("CO", "city_suffix"))
        # Ambiguous names are deliberately unresolved (Portland OR/ME, Columbia SC/MO/MD, Richmond VA/CA).
        for c in ("Portland", "Columbia", "Richmond", "Springfield", "lmao", "f"):
            self.assertEqual(na.resolve_state_from_city(c, "United States"), (None, "unresolved"), c)
        # A table city is not applied to the wrong country, and countries without units get nothing.
        self.assertEqual(na.resolve_state_from_city("Toronto", "United States"), (None, "unresolved"))
        self.assertEqual(na.resolve_state_from_city("Guadalajara", "Mexico"), (None, "country_without_units"))

    def test_classify_user_writes_provisional_lines_only_for_countries_with_units(self):
        self.assertEqual(na.classify_user({"user_id": 1, "country": "United States", "city": "Seattle"}),
                         {"geo": "WA", "geo_reason": "city_table", "provisional": True})
        self.assertEqual(na.classify_user({"user_id": 2, "country": "United States", "city": "Portland"}),
                         {"geo": None, "geo_reason": "unresolved", "provisional": True})
        self.assertIsNone(na.classify_user({"user_id": 3, "country": "United States", "city": ""}))
        self.assertIsNone(na.classify_user({"user_id": 4, "country": "Mexico", "city": "Monterrey"}))
        self.assertIsNone(na.classify_user({"user_id": 5, "country": None, "city": "Chicago"}))

    def test_geo_catalog_meets_the_contract(self):
        from scripts.North_America import geo
        from scripts.common.geo import validate_catalog
        cat = geo.catalog()
        self.assertEqual(validate_catalog(cat), [])
        self.assertEqual((cat["region"], cat["unit"], cat["provisional"]), ("North_America", "state", True))
        self.assertEqual(len(cat["units"]), 54 + 13)
        self.assertEqual({g["id"] for g in cat["groups"]}, {"us", "ca"})
        self.assertEqual(cat["seed_groups"], [])
        # unit ids are exactly what classify writes (postal codes)
        self.assertIn(na.place_state({"country_code": "US", "venue_address": "1 Main St, Austin, TX 78701, USA"})["geo"],
                      {u["id"] for u in cat["units"]})

    def test_unit_tables_are_consistent(self):
        self.assertTrue(na.GEO_PROVISIONAL)
        self.assertEqual(len(na.US_STATE_NAMES), 54)   # 50 states + DC + PR / VI / GU
        self.assertEqual(len(na.CA_PROVINCE_NAMES), 13)
        for code in na.CITY_STATE_PROVISIONAL.values():
            self.assertIn(code, na.STATE_NAMES, code)


if __name__ == "__main__":
    unittest.main()


class ContractAndNamingTests(unittest.TestCase):
    """The build reads the whole contract (scripts/common/event_contract.py); North America must provide it, provisionally."""

    def test_region_modules_meet_the_contract(self):
        from scripts.common import event_contract
        self.assertEqual(event_contract.check_region_modules("North_America"), [])

    def test_event_output_meets_the_contract(self):
        from types import SimpleNamespace
        from scripts.common import event_contract
        base = {"classifier_version": na.CLASSIFIER_VERSION, "event_id": 1, "tournament_name": "Smash Night #12",
                "event_name": "Ultimate Singles", "num_entrants": 40, "is_class_virtual": False, "parent_event_id": None,
                "class_letter": None, "is_offline": True, "results": {}}
        ctx = SimpleNamespace(attr={"timestamp": 1_757_000_000, "end_timestamp": None, "num_entrants": 40,
                                    "place": {"country_code": "US", "city": "Austin", "venue_address": "1 Main St, Austin, TX 78701, USA",
                                              "timezone": "America/Chicago"}},
                              tname="Smash Night #12", ename="Ultimate Singles", phases=None, class_phase_files=[])
        cur = na.classify_event(base, ctx)
        self.assertEqual(event_contract.check_event(cur), [])
        self.assertEqual(cur["place"]["geo"], "TX")
        self.assertEqual(cur["naming"]["series"], "Smash Night")
        self.assertEqual(cur["naming"]["series_number"], 12)
        self.assertFalse(cur["names"]["pre"]); self.assertFalse(cur["calendar"]["is_force_weekend_period"])

    def test_naming_strips_trailing_numbers_only(self):
        from scripts.North_America import naming
        for name, series, num in (("Smash Night #12", "Smash Night", 12), ("Weekly 45", "Weekly", 45),
                                  ("Genesis 9", "Genesis", 9), ("Frosty Faustings XVII", "Frosty Faustings XVII", None),
                                  ("Super Smash Con 2026", "Super Smash Con", 2026), ("Arcadian Vol. 3", "Arcadian", 3),
                                  ("Week 5: Melee & Ultimate", "Week 5: Melee & Ultimate", None)):
            self.assertEqual((naming.tournament_series(name), naming.tournament_series_number(name)), (series, num), name)
        self.assertTrue(naming.is_cancelled("Smash Night #12 (CANCELLED)"))
        self.assertFalse(naming.is_cancelled("Smash Night #12"))
        self.assertTrue(naming.is_test_page("test tournament"))
        self.assertFalse(naming.is_test_page("Contest of Champions"))

    def test_name_patterns_without_rules_never_match(self):
        for pat in (na.SPECIAL_RULES_PATTERN, na.UCHI_PATTERN, na.NON_SERIOUS_PATTERN):
            self.assertIsNone(pat.search("Smash Night #12 Invitational Items On"))
        from scripts.North_America import naming
        self.assertEqual(naming.community_series("Smash Night"), "Smash Night")

    def test_country_module(self):
        from scripts.North_America import country
        self.assertIn("US", country.COUNTRY_CODES)
        self.assertFalse(country.is_overseas_country("United States"))
        self.assertFalse(country.is_overseas_country("Canada"))
        self.assertTrue(country.is_overseas_country("Japan"))
        self.assertFalse(country.is_overseas_country(None))
        self.assertEqual(country.country_ja("Canada"), "Canada")
