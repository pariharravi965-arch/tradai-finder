"""Overfit risk assessment heuristics."""
from __future__ import annotations

from typing import Sequence

import numpy as np

from .metrics import trades_to_metrics
from ..engines.backtest import TradeResult


def assess_overfit_risk(
    train_trades: Sequence[TradeResult],
    test_trades: Sequence[TradeResult],
    n_params: int = 3,
    n_coins: int = 1,
) -> dict:
    m_train = trades_to_metrics(train_trades)
    m_test = trades_to_metrics(test_trades)
    n_train = m_train.get("n_trades", 0)
    n_test = m_test.get("n_trades", 0)

    reasons = []
    risk = "LOW"

    if n_train < 30:
        reasons.append("SMALL_TRAIN_SAMPLE")
        risk = "HIGH"
    if n_test < 15:
        reasons.append("SMALL_TEST_SAMPLE")
        risk = "HIGH" if risk != "HIGH" else risk

    e_tr = m_train.get("expectancy", float("nan"))
    e_te = m_test.get("expectancy", float("nan"))
    if not np.isnan(e_tr) and e_tr > 0 and (np.isnan(e_te) or e_te <= 0):
        reasons.append("TRAIN_POSITIVE_TEST_NEGATIVE")
        risk = "HIGH"
    elif not np.isnan(e_tr) and not np.isnan(e_te) and e_tr > 0:
        deg = (e_tr - e_te) / abs(e_tr) * 100
        if deg > 70:
            reasons.append(f"HIGH_DEGRADATION_{deg:.0f}PCT")
            risk = "HIGH"
        elif deg > 40:
            reasons.append(f"MODERATE_DEGRADATION_{deg:.0f}PCT")
            if risk == "LOW":
                risk = "MEDIUM"

    if n_params > 5 and n_train < 100:
        reasons.append("MANY_PARAMS_SMALL_SAMPLE")
        risk = "HIGH"

    if n_coins == 1 and n_train < 50:
        reasons.append("SINGLE_COIN_OPTIMIZATION")
        if risk == "LOW":
            risk = "MEDIUM"

    return {
        "overfit_risk": risk,
        "reasons": reasons,
        "train_metrics": m_train,
        "test_metrics": m_test,
    }
