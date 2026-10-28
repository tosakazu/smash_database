"""fetch_upcoming_entrants: seeds / pools of the first phase are added only when its bracket and seeding are published.

start.gg event.publishing.bracket / .seeding is a bool for the whole event or {"<phase id>": bool} (2026-10-28, e.g.
新京都DSW#75 {"2408538": false}). Unpublished phases are not returned by the API at all.
"""
import unittest
from unittest import mock

from scripts.common import fetch_upcoming_entrants as fue


def _seed(num, pg, uid=None, tag=''):
    return {'seedNum': num, 'phaseGroup': {'id': pg}, 'entrant': {'participants': [{'user': {'id': uid} if uid else None, 'player': {'gamerTag': tag}}]}}


def _gql(groups, seeds):
    def f(token, query, variables, retries=4):
        if query is fue.PHASE_GROUPS_QUERY:
            return {'phase': {'phaseGroups': {'pageInfo': {'totalPages': 1}, 'nodes': groups}}}
        if query is fue.PHASE_SEEDS_QUERY:
            return {'phase': {'seeds': {'pageInfo': {'totalPages': 1}, 'nodes': seeds}}}
        raise AssertionError(query)
    return f


PHASES = [{'id': 20, 'name': 'Top 8', 'phaseOrder': 2}, {'id': 10, 'name': 'Pools', 'phaseOrder': 1},
          {'id': 5, 'name': 'Exhibition', 'phaseOrder': 0, 'isExhibition': True}]
GROUPS = [{'id': 101, 'displayIdentifier': 'A1', 'bracketUrl': 'https://www.start.gg/x/101'},
          {'id': 102, 'displayIdentifier': 'A2', 'bracketUrl': 'https://www.start.gg/x/102'}]


class UpcomingBracketTests(unittest.TestCase):
    def setUp(self):
        self.sleep = mock.patch.object(fue.time, 'sleep', lambda s: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    def test_published_flag_forms(self):
        self.assertTrue(fue._published(True, 10))
        self.assertFalse(fue._published(False, 10))
        self.assertTrue(fue._published({'10': True}, 10))
        self.assertFalse(fue._published({'10': False, '20': True}, 10))
        self.assertFalse(fue._published(None, 10))

    def test_first_phase_skips_exhibition(self):
        self.assertEqual(fue.first_phase(PHASES)['id'], 10)
        self.assertIsNone(fue.first_phase([]))

    def test_seeds_and_pools_when_published(self):
        ents = [{'uid': 1, 'tag': 'a'}, {'uid': 2, 'tag': 'b'}, {'uid': None, 'tag': 'guest'}, {'uid': 4, 'tag': 'd'}]
        seeds = [_seed(1, 101, 1), _seed(2, 102, 2), _seed(3, 101, None, 'guest'), {'seedNum': 4, 'phaseGroup': {'id': 102}, 'entrant': None}]
        meta = {'publishing': {'bracket': {'10': True}, 'seeding': True}, 'phases': PHASES}
        with mock.patch.object(fue, 'gql', _gql(GROUPS, seeds)):
            b = fue.fetch_event_bracket('t', meta, ents)
        self.assertEqual(b, {'phase_id': 10, 'phase': 'Pools', 'pools': {
            '101': {'ident': 'A1', 'url': 'https://www.start.gg/x/101'}, '102': {'ident': 'A2', 'url': 'https://www.start.gg/x/102'}}})
        self.assertEqual([(e.get('seed'), e.get('pg')) for e in ents], [(1, 101), (2, 102), (3, 101), (None, None)])

    def test_nothing_when_unpublished_or_unseeded(self):
        for pub in ({'bracket': False, 'seeding': True}, {'bracket': True, 'seeding': {'10': False}}, {}):
            ents = [{'uid': 1, 'tag': 'a'}]
            with mock.patch.object(fue, 'gql', side_effect=AssertionError('no query when unpublished')):
                self.assertIsNone(fue.fetch_event_bracket('t', {'publishing': pub, 'phases': PHASES}, ents))
            self.assertEqual(ents, [{'uid': 1, 'tag': 'a'}])
        ents = [{'uid': 1, 'tag': 'a'}]
        with mock.patch.object(fue, 'gql', _gql(GROUPS, [])):
            self.assertIsNone(fue.fetch_event_bracket('t', {'publishing': {'bracket': True, 'seeding': True}, 'phases': PHASES}, ents))
        self.assertEqual(ents, [{'uid': 1, 'tag': 'a'}])


if __name__ == '__main__':
    unittest.main()
