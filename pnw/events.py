from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SessionRef:
    harness: str
    profile: str
    path: Path
    last_write: float


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    context_tokens: int | None = None
    context_limit: int | None = None


@dataclass(frozen=True)
class Event:
    harness: str
    profile: str
    session_path: Path
    event_id: str
    timestamp: str
    role: str
    text: str = ""
    stop_reason: str = ""
    error_text: str = ""
    model: str = ""
    provider: str = ""
    tool_call_id: str = ""
    tool_result_for: str = ""
    usage: Usage = field(default_factory=Usage)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def stable_key(self) -> str:
        detail = self.error_text or self.stop_reason or self.text[:120]
        return f"{self.session_path}|{self.event_id}|{self.timestamp}|{detail}"
