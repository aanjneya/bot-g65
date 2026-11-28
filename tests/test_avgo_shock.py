import unittest
from types import SimpleNamespace

from bot.avgo_shock import AvgoShockVeto


def book(bid):
    return SimpleNamespace(bids=[SimpleNamespace(price=bid)], asks=[SimpleNamespace(price=round(bid + 0.10, 2))])


class TestAvgoShockVeto(unittest.TestCase):
    def test_rise_pulls_ask_once_and_expires(self):
        guard = AvgoShockVeto()
        for t in range(5):
            self.assertIsNone(guard.observe(t, book(200.0)))
        for t in range(5, 15):
            self.assertEqual(guard.observe(t, book(200.3)), 'ask')
        self.assertIsNone(guard.observe(15, book(200.3)))

    def test_new_opposite_event_switches_side(self):
        guard = AvgoShockVeto()
        for t in range(5):
            guard.observe(t, book(200.0))
        self.assertEqual(guard.observe(5, book(200.3)), 'ask')
        self.assertEqual(guard.observe(6, book(199.7)), 'bid')

    def test_stale_anchor_and_missing_book_do_not_trigger(self):
        guard = AvgoShockVeto()
        self.assertIsNone(guard.observe(0, book(200.0)))
        self.assertIsNone(guard.observe(20, book(199.0)))
        self.assertIsNone(guard.observe(21, None))
        self.assertIsNone(guard.observe(25, book(198.0)))
