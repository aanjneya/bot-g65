"""Unit & MockExchange integration tests for the 6 distinct non-overfit Strategy Lab winners."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_DIR = os.path.join(ROOT, "bot")
for p in (ROOT, BOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from bot.mock_exchange import MockExchange  # noqa: E402
from strat_top1_blind_kalman import BlindGraphKalmanBot  # noqa: E402
from strat_top2_delay_deconv import DelayDeconvWaveBot  # noqa: E402
from strat_top3_pair_glft import BlockPairGLFTBot  # noqa: E402
from strat_top4_micro_obi import StudentBookMicroPennyBot  # noqa: E402
from strat_top5_velocity_unpin import VelocityUnpinMakerSniper  # noqa: E402
from strat_top6_omnibus import ApexOmnibusChampion  # noqa: E402
from strat_top7_allweather import ApexAllWeatherBot  # noqa: E402


ALL_SIX = [
    ("Top1_BlindGraphKalman", BlindGraphKalmanBot),
    ("Top2_DelayDeconvWave", DelayDeconvWaveBot),
    ("Top3_BlockPairGLFT", BlockPairGLFTBot),
    ("Top4_StudentMicroPenny", StudentBookMicroPennyBot),
    ("Top5_VelocityUnpin", VelocityUnpinMakerSniper),
    ("Top6_ApexOmnibus", ApexOmnibusChampion),
    ("Top7_ApexAllWeather", ApexAllWeatherBot),
]


class TestStrategyLabTop6(unittest.TestCase):
    def test_all_six_distinct_strategies_obey_optibook_limits_on_mock_exchange(self):
        for label, cls in ALL_SIX:
            with self.subTest(strategy=label):
                ex = MockExchange(max_pos=100, seed=42)
                ex.connect()
                strat = cls(ex)
                for _ in range(40):
                    ex.step_market()
                    strat.step()
                    positions = ex.get_positions()
                    for inst_id, pos in positions.items():
                        self.assertLessEqual(abs(pos), 100, f"{label} exceeded +-100 pos limit on {inst_id}")
                        orders = ex.get_outstanding_orders(inst_id)
                        tot_open = sum(int(o.volume) for o in orders.values())
                        self.assertLessEqual(tot_open, 200, f"{label} exceeded 200 open vol on {inst_id}")
                        bids = [o.price for o in orders.values() if o.side == "bid"]
                        asks = [o.price for o in orders.values() if o.side == "ask"]
                        if bids and asks:
                            self.assertLess(max(bids), min(asks), f"{label} self-crossed resting orders on {inst_id}")
                    self.assertLessEqual(len(strat.req_ts), 24, f"{label} exceeded 24 req/s safety cap")


if __name__ == "__main__":
    unittest.main()
