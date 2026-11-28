import unittest
from types import SimpleNamespace as NS

from bot.passive_evidence import PassiveEntryEvidence


class EvidenceTests(unittest.TestCase):
    def test_queue_direction_and_maturity(self):
        g = PassiveEntryEvidence()
        g.observe(0.0, {}, {})
        levels = [NS(price=10.0, volume=4)]
        def arm():
            return g.entry_lots('X', 'ask', 10.0, 0.1, 5, 0, levels)
        def buy(n):
            return NS(price=10.0, volume=n, aggressor_side='bid', buyer='other')
        self.assertEqual(arm(), 1)
        g.observe(0.25, {'X': [buy(4)]}, {'X': 10.0})
        self.assertFalse(g.pending)  # queue ahead only, not our lot
        arm()
        g.observe(0.5, {'X': [buy(3), buy(2)]}, {'X': 10.0})
        self.assertIn(('X', 'ask'), g.pending)
        g.observe(5.25, {}, {'X': 9.8})
        self.assertFalse(g.history['X', 'ask'])
        g.observe(5.5, {}, {'X': 9.8})
        self.assertAlmostEqual(g.history['X', 'ask'][0][1], 2.0)

    def test_sizing_is_side_specific_and_keeps_learning(self):
        g = PassiveEntryEvidence()
        g.observe(20.0, {}, {})
        g.history['X', 'bid'].extend((float(i), -2.0) for i in range(6))
        self.assertEqual(g.entry_lots('X', 'bid', 10.0, 0.1, 5, 0, []), 0)
        self.assertEqual(g.entry_lots('X', 'ask', 10.2, 0.1, 5, 1, []), 0)
        tr = NS(aggressor_side='ask', price=9.9, volume=1, seller='other')
        g.observe(20.25, {'X': [tr]}, {'X': 10.0})
        self.assertIn(('X', 'bid'), g.pending)  # blocked order still sampled
        self.assertNotIn(('X', 'ask'), g.pending)
        g.history['X', 'ask'].extend((float(i), 2.0) for i in range(6))
        self.assertEqual(g.entry_lots('X', 'ask', 10.2, 0.1, 3, 0, []), 3)
        g.observe(400.0, {}, {'X': 10.0})
        self.assertEqual(g.entry_lots('X', 'ask', 10.2, 0.1, 3, 0, []), 1)
