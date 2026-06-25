from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WatchState:
    handled: set[str] = field(default_factory=set)
    last_nudge_at: float = 0.0

    @classmethod
    def load(cls, path: Path) -> "WatchState":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            handled=set(raw.get("handled", [])),
            last_nudge_at=float(raw.get("last_nudge_at", 0.0) or 0.0),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "handled": sorted(self.handled)[-500:],
            "last_nudge_at": self.last_nudge_at,
            "saved_at": time.time(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
