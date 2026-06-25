from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable

from .events import Event, SessionRef, Usage


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home())


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "thinking", "message", "error", "errorMessage"):
            if value.get(key):
                parts.append(str(value[key]))
        return "\n".join(parts)
    if isinstance(value, list):
        return "\n".join(_as_text(part) for part in value if part is not None)
    return str(value)


def _usage_from_dict(value: object) -> Usage:
    if not isinstance(value, dict):
        return Usage()
    return Usage(
        input_tokens=value.get("input")
        or value.get("input_tokens")
        or value.get("prompt_tokens"),
        output_tokens=value.get("output")
        or value.get("output_tokens")
        or value.get("completion_tokens"),
        context_tokens=value.get("totalTokens")
        or value.get("total_tokens")
        or value.get("context_tokens"),
        context_limit=value.get("context_limit") or value.get("max_model_len"),
    )


class HarnessAdapter(ABC):
    harness: str

    def __init__(
        self,
        root: Path | None = None,
        profile: str = "",
        max_profiles: int = 8,
    ) -> None:
        self.root = root or self.default_root()
        self.profile = profile
        self.max_profiles = max_profiles

    @classmethod
    @abstractmethod
    def default_root(cls) -> Path:
        raise NotImplementedError

    @abstractmethod
    def discover_sessions(self) -> list[SessionRef]:
        raise NotImplementedError

    @abstractmethod
    def parse_line(self, line: str, session: SessionRef) -> Event | None:
        raise NotImplementedError

    def latest_events(self, session: SessionRef, tail: int = 160) -> list[Event]:
        lines = _tail_lines(session.path, tail)
        events: list[Event] = []
        for line in lines:
            event = self.parse_line(line, session)
            if event:
                events.append(event)
        return events


class JsonlProfileAdapter(HarnessAdapter):
    session_glob = "sessions/**/*.jsonl"

    def discover_sessions(self) -> list[SessionRef]:
        if not self.root.exists():
            return []
        profile_dirs: Iterable[Path]
        if self.profile:
            profile_dirs = [self.root / self.profile]
        else:
            profile_dirs = sorted(
                (p for p in self.root.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[: self.max_profiles]

        sessions: list[SessionRef] = []
        for profile_dir in profile_dirs:
            if not profile_dir.exists():
                continue
            files = sorted(
                profile_dir.glob(self.session_glob),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                latest = files[0]
                sessions.append(
                    SessionRef(
                        harness=self.harness,
                        profile=profile_dir.name,
                        path=latest,
                        last_write=latest.stat().st_mtime,
                    )
                )
        return sorted(sessions, key=lambda s: s.last_write, reverse=True)

    def parse_line(self, line: str, session: SessionRef) -> Event | None:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return Event(
                harness=self.harness,
                profile=session.profile,
                session_path=session.path,
                event_id="raw",
                timestamp="",
                role="raw",
                text=line,
            )
        msg = obj.get("message") if isinstance(obj, dict) else {}
        if not isinstance(msg, dict):
            msg = {}
        content = msg.get("content")
        text = _as_text(content)
        role = str(msg.get("role") or obj.get("type") or "")
        stop_reason = str(
            msg.get("stopReason")
            or msg.get("stop_reason")
            or obj.get("stopReason")
            or obj.get("stop_reason")
            or ""
        )
        error_text = str(
            msg.get("errorMessage")
            or obj.get("errorMessage")
            or obj.get("error")
            or ""
        )
        tool_call_id = ""
        tool_result_for = str(msg.get("toolCallId") or obj.get("toolCallId") or "")
        for part in content if isinstance(content, list) else []:
            if isinstance(part, dict) and str(part.get("type", "")).lower() in {
                "toolcall",
                "tool_call",
            }:
                tool_call_id = str(part.get("id") or part.get("toolCallId") or "")
        return Event(
            harness=self.harness,
            profile=session.profile,
            session_path=session.path,
            event_id=str(obj.get("id") or obj.get("uuid") or session.path.stat().st_mtime_ns),
            timestamp=str(obj.get("timestamp") or msg.get("timestamp") or ""),
            role=role,
            text=text,
            stop_reason=stop_reason,
            error_text=error_text,
            model=str(msg.get("model") or msg.get("responseModel") or ""),
            provider=str(msg.get("provider") or ""),
            tool_call_id=tool_call_id,
            tool_result_for=tool_result_for,
            usage=_usage_from_dict(msg.get("usage")),
            raw=obj,
        )


class PiAdapter(JsonlProfileAdapter):
    harness = "pi"

    @classmethod
    def default_root(cls) -> Path:
        return _home() / ".pi" / "agent" / "launcher-profiles"


class OmpAdapter(JsonlProfileAdapter):
    harness = "omp"

    @classmethod
    def default_root(cls) -> Path:
        return _home() / ".omp" / "agent" / "launcher-profiles"


class OpenClaudeAdapter(HarnessAdapter):
    harness = "openclaude"

    @classmethod
    def default_root(cls) -> Path:
        return _home() / ".openclaude" / "projects"

    def discover_sessions(self) -> list[SessionRef]:
        if not self.root.exists():
            return []
        files = sorted(
            self.root.rglob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if self.profile:
            files = [p for p in files if self.profile.lower() in str(p).lower()]
        sessions = [
            SessionRef(
                harness=self.harness,
                profile=p.parent.name,
                path=p,
                last_write=p.stat().st_mtime,
            )
            for p in files[: self.max_profiles]
        ]
        return sessions

    def parse_line(self, line: str, session: SessionRef) -> Event | None:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return None
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        content = msg.get("content") if msg else obj.get("content")
        error_obj = obj.get("error")
        error_text = _as_text(error_obj)
        text = _as_text(content) or error_text
        subtype = str(obj.get("subtype") or "")
        if subtype:
            text = f"{subtype}\n{text}".strip()
        return Event(
            harness=self.harness,
            profile=session.profile,
            session_path=session.path,
            event_id=str(obj.get("uuid") or obj.get("id") or session.path.stat().st_mtime_ns),
            timestamp=str(obj.get("timestamp") or ""),
            role=str(obj.get("type") or msg.get("role") or ""),
            text=text,
            stop_reason=str(msg.get("stop_reason") or msg.get("stopReason") or ""),
            error_text=error_text,
            model=str(msg.get("model") or ""),
            provider="openclaude",
            usage=_usage_from_dict(msg.get("usage")),
            raw=obj,
        )


class OpenCodeAdapter(HarnessAdapter):
    harness = "opencode"

    @classmethod
    def default_root(cls) -> Path:
        return _home() / ".opencode"

    def discover_sessions(self) -> list[SessionRef]:
        # OpenCode support starts conservative. Some installs expose no durable
        # transcript. v2 can still report that and later add wrapper capture.
        if not self.root.exists():
            return []
        candidates = sorted(
            [
                p
                for p in self.root.rglob("*")
                if p.is_file()
                and p.suffix.lower() in {".jsonl", ".log"}
                and any(token in p.name.lower() for token in ("session", "history", "log"))
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return [
            SessionRef(self.harness, p.parent.name, p, p.stat().st_mtime)
            for p in candidates[: self.max_profiles]
        ]

    def parse_line(self, line: str, session: SessionRef) -> Event | None:
        try:
            obj = json.loads(line)
            text = _as_text(obj)
            event_id = str(obj.get("id") or obj.get("uuid") or session.path.stat().st_mtime_ns)
            timestamp = str(obj.get("timestamp") or "")
            role = str(obj.get("role") or obj.get("type") or "log")
        except json.JSONDecodeError:
            text = line
            event_id = "raw"
            timestamp = ""
            role = "log"
        return Event(
            harness=self.harness,
            profile=session.profile,
            session_path=session.path,
            event_id=event_id,
            timestamp=timestamp,
            role=role,
            text=text,
        )


class GenericLogAdapter(HarnessAdapter):
    harness = "generic"

    @classmethod
    def default_root(cls) -> Path:
        return _home() / ".pi-nudge-watchdog" / "wrapped"

    def discover_sessions(self) -> list[SessionRef]:
        if not self.root.exists():
            return []
        files = sorted(
            [
                p
                for p in self.root.rglob("*")
                if p.is_file() and p.suffix.lower() in {".jsonl", ".log", ".txt"}
            ],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if self.profile:
            files = [p for p in files if self.profile.lower() in str(p).lower()]
        return [
            SessionRef(self.harness, p.parent.name, p, p.stat().st_mtime)
            for p in files[: self.max_profiles]
        ]

    def parse_line(self, line: str, session: SessionRef) -> Event | None:
        if not line.strip():
            return None
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            text = _as_text(obj) or json.dumps(obj, ensure_ascii=False)
            event_id = str(obj.get("id") or obj.get("uuid") or session.path.stat().st_mtime_ns)
            timestamp = str(obj.get("timestamp") or "")
            role = str(obj.get("role") or obj.get("type") or "log")
        else:
            text = line
            event_id = "raw"
            timestamp = ""
            role = "log"
        return Event(
            harness=self.harness,
            profile=session.profile,
            session_path=session.path,
            event_id=event_id,
            timestamp=timestamp,
            role=role,
            text=text,
        )


def make_adapters(
    harness: str,
    profile: str = "",
    roots: dict[str, Path] | None = None,
    max_profiles: int = 8,
) -> list[HarnessAdapter]:
    roots = roots or {}
    classes = {
        "pi": PiAdapter,
        "omp": OmpAdapter,
        "openclaude": OpenClaudeAdapter,
        "opencode": OpenCodeAdapter,
        "generic": GenericLogAdapter,
    }
    names = list(classes) if harness == "all" else [harness]
    adapters: list[HarnessAdapter] = []
    for name in names:
        cls = classes.get(name)
        if not cls:
            raise ValueError(f"Unknown harness: {name}")
        adapters.append(cls(root=roots.get(name), profile=profile, max_profiles=max_profiles))
    return adapters


def _tail_lines(path: Path, count: int) -> list[str]:
    # JSONL session files can be large. This keeps memory bounded without
    # relying on platform-specific tail commands.
    if count <= 0:
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            end = fh.tell()
            size = min(end, 1024 * 1024)
            fh.seek(end - size)
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-count:]
