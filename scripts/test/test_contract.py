"""Region-neutral tests of the pieces every region shares: the event contract and the geo.json catalogue check.
Region rules and their tests live on the region's data branch (data-Japan: scripts/Japan/test_rules.py,
data-North_America: scripts/North_America/test_classify.py); main carries only scripts/common."""
import unittest

from scripts.common import event_contract
from scripts.common.geo import validate_catalog


def _catalog():
    return {
        "region": "Test", "unit": "state", "provisional": True, "names": {"en": "State"},
        "units": [{"id": "A", "order": 1, "name": {"en": "A"}, "group": "g"},
                  {"id": "B", "order": 2, "name": {"en": "B"}, "group": "g"}],
        "groups": [{"id": "g", "name": {"en": "G"}, "units": ["A", "B"]}],
        "seed_groups": [{"id": "ab", "name": {"en": "AB"}, "units": ["A", "B"], "default": True}],
    }


class GeoCatalogContractTests(unittest.TestCase):
    def test_valid_catalog_passes(self):
        self.assertEqual(validate_catalog(_catalog()), [])

    def test_violations_are_reported(self):
        c = _catalog(); c["units"][1]["id"] = "A"
        self.assertTrue(any("not unique" in e for e in validate_catalog(c)))
        c = _catalog(); c["groups"][0]["units"] = ["A"]
        self.assertTrue(any("cover" in e for e in validate_catalog(c)))
        c = _catalog(); c["seed_groups"][0]["units"].append("Z")
        self.assertTrue(any("unknown unit" in e for e in validate_catalog(c)))
        c = _catalog(); del c["seed_groups"]
        self.assertTrue(any("missing key" in e for e in validate_catalog(c)))


class EventContractTests(unittest.TestCase):
    def test_complete_event_passes(self):
        cur = {
            "names": {k: False for k in event_contract.NAMES_KEYS},
            "calendar": {"date": "2026-01-01", "end_date": "2026-01-01", "is_weekend_real": False, "is_force_weekend_period": False},
            "naming": {k: None for k in event_contract.NAMING_KEYS},
            "place": {"geo": None},
        }
        self.assertEqual(event_contract.check_event(cur), [])
        cur["calendar"] = None   # an event without a timestamp
        self.assertEqual(event_contract.check_event(cur), [])

    def test_missing_keys_are_reported(self):
        cur = {"names": {"pre": False}, "naming": {}, "place": {}}
        errs = event_contract.check_event(cur)
        for prefix in ("names: missing", "naming: missing", "place: missing", "calendar: missing"):
            self.assertTrue(any(e.startswith(prefix) for e in errs), prefix)
        cur = {"names": {k: "no" for k in event_contract.NAMES_KEYS}, "calendar": None,
               "naming": {k: None for k in event_contract.NAMING_KEYS}, "place": {"geo": None}}
        self.assertTrue(any(e.startswith("names: not bool") for e in event_contract.check_event(cur)))

    def test_unknown_region_is_reported(self):
        errs = event_contract.check_region_modules("No_Such_Region")
        self.assertTrue(errs and all("cannot import" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
