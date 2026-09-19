"""Cost engine — always show GROSS vs NET."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostModel:
    fee_rate: float = 0.0004
    slippage_bps: float = 2.0
    funding_per_period: float = 0.0  # optional average

    def round_trip_cost_frac(self) -> float:
        # entry + exit fees + 2x slippage
        return 2 * self.fee_rate + 2 * (self.slippage_bps / 10_000)

    def net_r(self, gross_r: float, holding_periods: int = 1) -> float:
        cost = self.round_trip_cost_frac()
        # approximate: cost relative to risk unit is handled in backtest via price
        return gross_r  # refined in backtest with actual prices
