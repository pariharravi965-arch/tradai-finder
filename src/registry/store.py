"""
Research registries — reproducible metadata (Parquet/JSON), not mystical state.

Registries:
  data_registry, feature_registry, hypothesis_registry, experiment_registry,
  backtest_registry, postmortem_registry, validation_registry, ai_feedback_registry
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from loguru import logger


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class RegistryStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str, ext: str = "jsonl") -> Path:
        return self.root / f"{name}.{ext}"

    def append(self, registry: str, record: dict[str, Any]) -> str:
        rid = record.get("id") or _id(registry[:3])
        record = {**record, "id": rid, "recorded_at": record.get("recorded_at") or _now()}
        path = self._path(registry)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return rid

    def load(self, registry: str) -> list[dict]:
        path = self._path(registry)
        if not path.exists():
            return []
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def to_parquet(self, registry: str) -> Optional[Path]:
        rows = self.load(registry)
        if not rows:
            return None
        out = self.root / f"{registry}.parquet"
        pd.DataFrame(rows).to_parquet(out, index=False)
        return out

    # --- typed helpers ---
    def register_hypothesis(self, **kwargs) -> str:
        required = {
            "hypothesis_id": kwargs.get("hypothesis_id") or _id("hyp"),
            "features": kwargs.get("features", []),
            "conditions": kwargs.get("conditions", {}),
            "timeframe": kwargs.get("timeframe"),
            "coin": kwargs.get("coin"),
            "sample_size": kwargs.get("sample_size", 0),
            "direction": kwargs.get("direction"),
            "expected_effect": kwargs.get("expected_effect"),
            "observed_effect": kwargs.get("observed_effect"),
            "confidence": kwargs.get("confidence"),
            "p_value": kwargs.get("p_value"),
            "effect_size": kwargs.get("effect_size"),
            "stability": kwargs.get("stability"),
            "training_period": kwargs.get("training_period"),
            "validation_period": kwargs.get("validation_period"),
            "status": kwargs.get("status", "DISCOVERED"),  # DISCOVERED|VALIDATING|VALIDATED|REJECTED|UNSTABLE|DECAYED
        }
        return self.append("hypothesis_registry", required)

    def register_experiment(self, **kwargs) -> str:
        rec = {
            "experiment_id": kwargs.get("experiment_id") or _id("exp"),
            "parent_id": kwargs.get("parent_id"),
            "hypothesis_ids": kwargs.get("hypothesis_ids", []),
            "dataset_version": kwargs.get("dataset_version"),
            "feature_version": kwargs.get("feature_version"),
            "split": kwargs.get("split"),  # 8d|60d|1y|wf
            "seed": kwargs.get("seed"),
            "status": kwargs.get("status", "RUNNING"),
            "metrics": kwargs.get("metrics", {}),
            "notes": kwargs.get("notes", ""),
        }
        return self.append("experiment_registry", rec)

    def register_ai_feedback(self, **kwargs) -> str:
        rec = {
            "feedback_id": kwargs.get("feedback_id") or _id("aif"),
            "what_failed": kwargs.get("what_failed"),
            "why": kwargs.get("why"),
            "data_missing": kwargs.get("data_missing", []),
            "feature_may_help": kwargs.get("feature_may_help", []),
            "hypothesis_to_test": kwargs.get("hypothesis_to_test", []),
            "do_not_trust": kwargs.get("do_not_trust", []),
            "decayed": kwargs.get("decayed", []),
            "remained_stable": kwargs.get("remained_stable", []),
            "test_next": kwargs.get("test_next", []),
            "evidence_refs": kwargs.get("evidence_refs", []),
        }
        return self.append("ai_feedback_registry", rec)
