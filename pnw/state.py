from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WatchState:
    handled: set[str] = field(default_factory=set)
    last_nudge_at: float = 0.0
    nudge_counts: dict[str, int] = field(default_factory=dict)
    suppressed_sessions: set[str] = field(default_factory=set)

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
            nudge_counts={str(k): int(v) for k, v in raw.get("nudge_counts", {}).items()},
            suppressed_sessions=set(raw.get("suppressed_sessions", [])),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "handled": sorted(self.handled)[-500:],
            "last_nudge_at": self.last_nudge_at,
            "nudge_counts": dict(sorted(self.nudge_counts.items())[-500:]),
            "suppressed_sessions": sorted(self.suppressed_sessions)[-500:],
            "saved_at": time.time(),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def nudge_key(self, session_path: Path, kind: str) -> str:
        return f"{session_path}|{kind}"
