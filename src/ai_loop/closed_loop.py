"""
Closed AI learning loop:

postmortem → propose hypotheses/features → register experiment →
(validate externally) → knowledge update → next experiment queue
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ..registry.store import RegistryStore
from .knowledge import build_ai_improvement_plan, write_plan


class LearningLoop:
    def __init__(self, registry: RegistryStore, queue_path: Path):
        self.registry = registry
        self.queue_path = Path(queue_path)
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    def load_queue(self) -> list[dict]:
        if not self.queue_path.exists():
            return []
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def save_queue(self, items: list[dict]) -> None:
        self.queue_path.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")

    def from_postmortem(
        self,
        postmortem_summary: dict,
        unknown_clusters: dict | None,
        rejected_factors: list[str],
        data_gaps: list[str],
    ) -> list[dict]:
        """Generate next experiment queue items from failures."""
        queue = self.load_queue()
        proposals = []

        for tag, count in (postmortem_summary.get("top_reason_tags") or postmortem_summary.get("reason_counts") or {}).items() if isinstance(postmortem_summary.get("top_reason_tags"), dict) else []:
            proposals.append({
                "type": "FEATURE_OR_FILTER",
                "reason_tag": tag,
                "priority": int(count) if isinstance(count, (int, float)) else 1,
                "action": f"Add/no-trade rule around {tag}",
                "status": "QUEUED",
            })
        # unknown clusters → new hypothesis
        for cl in (unknown_clusters or {}).get("clusters", [])[:5]:
            proposals.append({
                "type": "HYPOTHESIS",
                "pattern": cl.get("pattern"),
                "count": cl.get("count"),
                "action": "Name failure category + test as no-trade or inverse signal",
                "status": "QUEUED",
            })
        for f in rejected_factors[:10]:
            proposals.append({
                "type": "DO_NOT_TRUST",
                "factor": f,
                "action": "Downweight in committee / exclude from auto signals",
                "status": "QUEUED",
            })
        for g in data_gaps[:5]:
            proposals.append({
                "type": "DATA_GAP",
                "gap": g,
                "action": "Keep collector running or attach external adapter",
                "status": "QUEUED",
            })

        # dedupe by action string
        existing = {x.get("action") for x in queue}
        for p in proposals:
            p["queued_at"] = datetime.now(timezone.utc).isoformat()
            if p.get("action") not in existing:
                queue.append(p)
                self.registry.register_experiment(
                    status="QUEUED",
                    notes=p.get("action"),
                    hypothesis_ids=[],
                )
        self.save_queue(queue)
        logger.info(f"Learning loop queue size={len(queue)}")
        return queue

    def mark_done(self, action: str, result: str) -> None:
        q = self.load_queue()
        for item in q:
            if item.get("action") == action:
                item["status"] = result  # VALIDATED|REJECTED|DONE
                item["finished_at"] = datetime.now(timezone.utc).isoformat()
        self.save_queue(q)
