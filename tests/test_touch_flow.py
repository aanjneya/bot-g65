import unittest
from types import SimpleNamespace as NS

from bot.touch_flow import TouchFlow


class TouchFlowTest(unittest.TestCase):
    def setUp(self):
        self.flow = TouchFlow()

    def poll(self, t, prints=(), b=10.0, a=10.1, own=False):
        book = NS(bids=[NS(price=b)], asks=[NS(price=a)])
        return self.flow.observe(t, {'X': book}, {'X': prints}, {'X': [1] if own else []}, 'team')

    def trade(self, side, px, buyer='other'):
        return NS(aggressor_side=side, price=px, volume=2, buyer=buyer, seller='external')

    def test_requires_recent_both_sides_on_same_touch(self):
        sell, buy = self.trade('ask', 10.0), self.trade('bid', 10.1)
        self.assertFalse(self.poll(0, [buy, sell]))
        self.assertFalse(self.poll(0.25, [sell]))
        self.assertFalse(self.poll(0.5, [self.trade('ask', 10.1), self.trade('bid', 10.1, 'team')]))
        self.assertEqual({'X'}, self.poll(1, [buy]))
        self.assertFalse(self.poll(3.5, [buy]))

    def test_change_or_own_fill_invalidates_evidence(self):
        prints = [self.trade('ask', 10.0), self.trade('bid', 10.1)]
        self.poll(0)
        self.assertEqual({'X'}, self.poll(0.25, prints))
        self.assertFalse(self.poll(0.5, prints, own=True))
        self.assertFalse(self.poll(0.75))
        self.assertEqual({'X'}, self.poll(1, prints))
        self.assertFalse(self.poll(1.25, [self.trade('ask', 9.9), prints[1]], b=9.9))
        self.assertFalse(self.poll(1.5, [prints[1]], b=9.9))
