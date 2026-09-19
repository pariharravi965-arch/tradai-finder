"""Run versioning & experiment tracking."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def get_run_id(prefix: str = "run") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"{prefix}_{ts}_{short}"


@dataclass
class RunContext:
    run_id: str
    finder_version: str
    data_version: str
    feature_version: str
    strategy_version: str
    law_version: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    config_snapshot: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
