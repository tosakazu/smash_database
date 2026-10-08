"""region_rules.json: the region's rules evaluated as data for the ranking build."""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.common import region_rules as rr

CLF = SimpleNamespace(TIMEZONE="Asia/Tokyo", CLASSIFIER_VERSION=99, SMACOMI_FORCE_WEEKDAY_MAX_NENT=30,
                      FORCE_WEEKEND_MIN_NENT=80, NON_SERIOUS_PATTERN=re.compile("お遊び|身内"),
                      UCHI_PATTERN=re.compile("身内"), SPECIAL_RULES_PATTERN=re.compile("お遊び"),
                      RESTRICTED_PATTERN=re.compile("制限"))
NAMING = SimpleNamespace(tournament_series=lambda n: n.split("#")[0].strip(),
                         tournament_series_number=lambda n: int(n.split("#")[1]) if "#" in n else None,
                         tournament_award_label=lambda n: n + "!", tournament_individual_label=lambda n: n.upper(),
                         CANCELLED_PATTERN=re.compile("中止"), TEST_PATTERN=re.compile("テスト"),
                         community_series=lambda s: {"A 拡大版": "A"}.get(s, s))
COUNTRY = SimpleNamespace(COUNTRY_CODES=("JP",), country_ja=lambda c: {"Japan": "日本"}.get(c))


class RegionRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.region = Path(self.tmp.name) / "Japan"
        ev = self.region / "events" / "2026" / "10" / "01" / "A_3" / "Singles"
        ev.mkdir(parents=True)
        (ev / "attr.json").write_text(json.dumps({"tournament_name": "A #3"}), encoding="utf-8")
        (ev / "derived.json").write_text(json.dumps({"naming": {"series_event": "A 拡大版"}}), encoding="utf-8")
        (self.region / "tournaments.jsonl").write_text(json.dumps({"tournament_id": 1, "name": "B 身内 中止"}) + "\n",
                                                       encoding="utf-8")
        (self.region / "users.jsonl").write_text(json.dumps({"user_id": 1, "country": "Japan"}) + "\n"
                                                 + json.dumps({"user_id": 2, "country": "France"}) + "\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_contents(self):
        r = rr.build_rules(CLF, NAMING, COUNTRY, self.region, self.region / "events")
        self.assertEqual(r["constants"]["TIMEZONE"], "Asia/Tokyo")
        self.assertEqual(r["country_codes"], ["JP"])
        self.assertEqual(r["countries"], {"France": {"ja": None}, "Japan": {"ja": "日本"}})
        self.assertEqual(set(r["tournament_names"]), {"A #3", "B 身内 中止"})
        self.assertEqual(r["tournament_names"]["A #3"], {"series": "A", "series_number": 3, "award_label": "A #3!",
                                                         "individual_label": "A #3", "cancelled": False,
                                                         "test_page": False})
        self.assertTrue(r["tournament_names"]["B 身内 中止"]["cancelled"])
        self.assertEqual(r["series"], {"A 拡大版": {"community": "A", "non_serious": False, "uchi": False,
                                                 "special_rules": False, "restricted": False}})

    def test_written_only_when_changed(self):
        w1, _ = rr.write_region_rules(CLF, NAMING, COUNTRY, self.region, self.region / "events")
        mtime = os.stat(self.region / rr.FILE).st_mtime_ns
        w2, _ = rr.write_region_rules(CLF, NAMING, COUNTRY, self.region, self.region / "events")
        self.assertEqual((w1, w2), (True, False))
        self.assertEqual(os.stat(self.region / rr.FILE).st_mtime_ns, mtime)


if __name__ == "__main__":
    unittest.main()
