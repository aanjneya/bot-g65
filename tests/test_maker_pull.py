import unittest
from types import SimpleNamespace as NS

from bot.maker_pull import MakerPull


def book(b, a):
    return {'X': NS(bids=[NS(price=b)], asks=[NS(price=a)])}


class MakerPullTests(unittest.TestCase):
    def warm(self):
        p = MakerPull()
        for t in range(5):
            self.assertFalse(p.observe(t, book(10, 10.1)))
        return p

    def test_rise_and_full_retrace(self):
        p = self.warm()
        self.assertEqual(p.observe(5, book(10.3, 10.4)), {('X', 'ask')})
        self.assertEqual(p.observe(6, book(10.1, 10.2)), {('X', 'ask')})
        self.assertFalse(p.observe(7, book(10, 10.1)))

    def test_fall_expires_without_rearming_on_unchanged_book(self):
        p = self.warm()
        for t in range(5, 20):
            self.assertEqual(p.observe(t, book(9.7, 9.8)), {('X', 'bid')})
        self.assertFalse(p.observe(20, book(9.7, 9.8)))

    def test_spread_widening_is_not_a_directional_shock(self):
        p = self.warm()
        self.assertFalse(p.observe(5, book(10, 10.8)))

    def test_gap_is_not_a_five_second_observation(self):
        p = self.warm()
        self.assertFalse(p.observe(14, book(10.5, 10.6)))
