import unittest
from types import SimpleNamespace as NS

from bot.cycle_budget import CycleBudget


def fill(side, vol, price):
    return NS(side=side, volume=vol, price=price)


class CycleBudgetTest(unittest.TestCase):
    def test_partial_close_reversal_and_inherited_position(self):
        b = CycleBudget()
        b._fills('X', 0, 3, 0.1, [])  # inherited long, no basis
        b._fills('X', 1, -2, 0.1, [fill('ask', 5, 10.0)])
        self.assertNotIn(('X', 1), b.blocks)
        b._fills('X', 2, -1, 0.1, [fill('bid', 1, 9.8)])
        self.assertNotIn(('X', -1), b.blocks)
        b._fills('X', 3, 0, 0.1, [fill('bid', 1, 10.1)])
        self.assertEqual(list(b.blocks['X', -1]), [[0, 1, 2, 1]])

    def test_losing_side_expires_without_blocking_other_side(self):
        b = CycleBudget()
        b._fills('X', 1, 1, 0.1, [fill('bid', 1, 10)])
        b._fills('X', 2, 0, 0.1, [fill('ask', 1, 9)])
        self.assertEqual(b.entry(3, 'X', 1, 5, True, 100), 0)
        self.assertEqual(b.entry(3, 'X', -1, 5, True, 100), 5)
        self.assertEqual(b.entry(901, 'X', 1, 5, True, 100), 5)

    def test_ioc_addition_and_snapshot_mismatch_excluded(self):
        b = CycleBudget()
        b.flag_opening_iocs(1, ['X'])
        b._fills('X', 2, 2, 0.1, [fill('bid', 2, 10)])
        b._fills('X', 9, 0, 0.1, [fill('ask', 2, 11)])
        b._fills('X', 10, 2, 0.1, [fill('bid', 1, 10)])  # one missing lot
        b._fills('X', 11, 0, 0.1, [fill('ask', 2, 11)])
        self.assertFalse(b.blocks)

    def test_growth_needs_completed_minutes_and_fresh_exit_demand(self):
        b = CycleBudget()
        for minute in range(4):
            for k in range(3):
                t = minute * 60 + k * 2
                b._fills('X', t, 1, 0.1, [fill('bid', 1, 9.9)])
                b._fills('X', t + 1, 0, 0.1, [fill('ask', 1, 10.0)])
        inst = {'X': NS(tick_size=0.1)}
        tape = {'X': [NS(aggressor_side='bid', price=10.0, volume=20)]}
        b.observe(239, inst, {}, {}, tape)
        self.assertEqual(b.entry(239, 'X', 1, 5, True, 100), 5)
        self.assertEqual(b.entry(240, 'X', 1, 5, True, 100), 5)
        b.observe(240, inst, {}, {}, tape)
        self.assertEqual(b.entry(240, 'X', 1, 5, True, 100), 10)
        self.assertEqual(b.entry(240, 'X', 1, 0, True, 100), 0)
        self.assertEqual(b.entry(240, 'X', 1, 5, False, 100), 5)
        self.assertEqual(b.entry(246, 'X', 1, 5, True, 100), 5)
