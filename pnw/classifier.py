from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .events import Event, SessionRef


RECOVERABLE_RE = re.compile(
    r"Proxy error:\s*(?:timed out|<urlopen error \[WinError 10060\]|.*WinError 10060.*)"
    r"|terminated"
    r"|Request timed out"
    r"|Connection error"
    r"|Retry failed after \d+ attempts"
    r"|Aborted after \d+ retry attempts"
    r"|api_error",
    re.IGNORECASE,
)

CONTEXT_RE = re.compile(
    r"context overflow"
    r"|exceeds the available context size"
    r"|session context is too large"
    r"|too large to continue safely"
    r"|compaction failed"
    r"|auto-compaction failed"
    r"|turn prefix summarization failed",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Decision:
    kind: str
    allow_nudge: bool
    reason: str
    event: Event | None = None


def classify(events: list[Event], session: SessionRef, now: float | None = None) -> Decision:
    now = now or time.time()
    if not events:
        return Decision("unknown", False, "No events found.")

    latest_failure: Event | None = None
    for event in reversed(events):
        text = _event_text(event)
        if CONTEXT_RE.search(text):
            return Decision(
                "context_or_compaction_failure",
                False,
                "Context or compaction failure detected; refusing blind continue.",
                event,
            )
        if event.stop_reason.lower() == "length":
            latest_failure = event
            return Decision(
                "max_output_truncation",
                True,
                "Assistant stopped due to output length; continue is safe.",
                event,
            )
        if RECOVERABLE_RE.search(text):
            latest_failure = event
            break

    if not latest_failure:
        if _has_active_tool_wait(events):
            return Decision(
                "active_tool_wait",
                False,
                "Latest state has an outstanding tool call/result flow.",
                events[-1],
            )
        if now - session.last_write < 20:
            return Decision(
                "active_generation",
                False,
                "Session file changed recently; waiting.",
                events[-1],
            )
        return Decision("unknown", False, "No recoverable failure detected.", events[-1])

    if _has_outstanding_continue(events):
        return Decision(
            "queued_nudge_exists",
            False,
            "A continue/nudge is already outstanding.",
            latest_failure,
        )

    return Decision(
        "recoverable_provider_failure",
        True,
        "Recoverable provider failure detected.",
        latest_failure,
    )


def _event_text(event: Event) -> str:
    return "\n".join(
        part
        for part in (event.text, event.error_text, event.stop_reason, event.role)
        if part
    )


def _has_outstanding_continue(events: list[Event]) -> bool:
    outstanding = False
    for event in events[-160:]:
        if event.role == "user" and event.text.strip().lower() in {
            "continue",
            "steering: continue",
        }:
            outstanding = True
            continue
        if outstanding and event.role in {"assistant", "toolResult", "tool_result"}:
            if event.stop_reason and event.stop_reason.lower() in {"error", "aborted"}:
                continue
            if event.tool_call_id or event.tool_result_for or event.text.strip():
                outstanding = False
    return outstanding


def _has_active_tool_wait(events: list[Event]) -> bool:
    open_calls: set[str] = set()
    for event in events[-80:]:
        if event.tool_call_id:
            open_calls.add(event.tool_call_id)
        if event.tool_result_for and event.tool_result_for in open_calls:
            open_calls.remove(event.tool_result_for)
    return bool(open_calls)
