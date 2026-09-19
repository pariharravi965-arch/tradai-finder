"""Feature engine — Phase 2: deep structure, VP/VWAP, sessions, derivatives, MTF, discovery."""
from __future__ import annotations

from typing import Optional

import pandas as pd
from loguru import logger

from .price_vol import add_price_volume_features
from .momentum import add_momentum_features
from .volatility import add_volatility_features
from .derivatives import add_funding_features, merge_oi_features
from ..structure.swings import add_structure_features
from ..structure.deep_structure import add_deep_structure
from ..regime.classifier import add_regime_labels
from ..volume_profile.vp_vwap import add_vwap_features, add_volume_profile_approx
from ..sessions.session_engine import add_session_features
from ..derivatives.oi_funding_engine import enrich_derivatives
from ..discovery.hypothesis_gen import generate_hypotheses


class FeatureEngine:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.fcfg = cfg.get("features", {})

    def transform(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        funding: Optional[pd.DataFrame] = None,
        oi: Optional[pd.DataFrame] = None,
        oi_status: str = "DATA_MISSING",
        htf_df: Optional[pd.DataFrame] = None,
        htf_label: str = "1h",
    ) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        out = df.copy().sort_values("open_time").reset_index(drop=True)

        out = add_price_volume_features(out, self.fcfg)
        out = add_volatility_features(out, self.fcfg)
        out = add_momentum_features(out, self.fcfg)
        out = add_structure_features(out, lookback=self.fcfg.get("structure_swing_lookback", 5))
        out = add_deep_structure(out, lookback=3)
        out = add_regime_labels(out, lookback=self.fcfg.get("regime_lookback", 50))
        out = add_session_features(out)
        out = add_vwap_features(out, session_col="session")
        out = add_volume_profile_approx(out, lookback=48, bins=20)

        if funding is not None and not funding.empty:
            out = add_funding_features(out, funding)
        else:
            out["funding_rate"] = float("nan")
            out["funding_available"] = 0

        if oi is not None and not oi.empty and oi_status != "DATA_MISSING":
            out = merge_oi_features(out, oi, status=oi_status)
        else:
            out["oi"] = float("nan")
            out["oi_available"] = 0
            out["oi_status"] = oi_status

        out = enrich_derivatives(out)

        # MTF
        if htf_df is not None and not htf_df.empty:
            from ..mtf.mtf_engine import add_mtf_context
            # HTF should already have structure cols if built similarly
            out = add_mtf_context(out, htf_df, htf_label=htf_label)
        else:
            out["htf_bias"] = "UNKNOWN"
            out["mtf_align"] = 0
            out["mtf_conflict"] = 0
            out["mtf_status"] = "NO_HTF"

        out = generate_hypotheses(out)
        out["symbol"] = symbol
        out["timeframe"] = timeframe
        logger.debug(f"Features {symbol} {timeframe}: {len(out)} rows × {len(out.columns)} cols")
        return out
