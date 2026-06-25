from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .events import Event, SessionRef


RECOVERABLE_RE = re.compile(
    r"Proxy error:\s*(?:timed out|<urlopen error \[WinError 10060\]|.*WinError 10060.*)"
    r"|WinError 10060"
    r"|A connection attempt failed"
    r"|properly respond after a period of time"
    r"|connected host has failed to respond"
    r"|Operation aborted"
    r"|\bterminated\b"
    r"|Request timed out"
    r"|Connection error"
    r"|Could not connect"
    r"|Retry failed after \d+ attempts"
    r"|Aborted after \d+ retry attempts"
    r"|api_error",
    re.IGNORECASE,
)

NON_RECOVERABLE_TOOL_ERROR_RE = re.compile(
    r"SyntaxError"
    r"|unterminated string literal"
    r"|unexpected EOF"
    r"|here-document .*delimited by end-of-file"
    r"|Command exited with code \d+",
    re.IGNORECASE | re.DOTALL,
)

HARNESS_BLOCK_RE = re.compile(
    r"Blocked a malformed local-model tool call"
    r"|Retry with one smaller, valid tool call only"
    r"|unsupported tool \w+",
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

    if _latest_assistant_completed(events):
        return Decision(
            "complete_or_idle",
            False,
            "Latest assistant response appears complete; ignoring older failures.",
            events[-1],
        )

    latest_user = _latest_meaningful_event(events, "user")
    latest_assistant = _latest_event(events, "assistant")
    if latest_user and (
        not latest_assistant
        or events.index(latest_user) > events.index(latest_assistant)
    ):
        if not _is_nudge_text(latest_user.text):
            return Decision(
                "awaiting_assistant",
                False,
                "Latest event is a new user turn; ignoring older failures.",
                latest_user,
            )

    if _has_active_tool_wait(events):
        return Decision(
            "active_tool_wait",
            False,
            "Latest state has an outstanding tool call/result flow.",
            events[-1],
        )

    cleared = _old_failure_cleared_by_later_progress(events)
    if cleared:
        return cleared

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
        if NON_RECOVERABLE_TOOL_ERROR_RE.search(text):
            return Decision(
                "tool_or_code_error",
                False,
                "Tool/code error detected; refusing to auto-continue the same failure.",
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
        if HARNESS_BLOCK_RE.search(text):
            latest_failure = event
            return Decision(
                "malformed_tool_call_blocked",
                True,
                "Harness blocked a malformed local-model tool call; retry nudge is safe.",
                event,
            )
        if RECOVERABLE_RE.search(text):
            latest_failure = event
            break

    if not latest_failure:
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
        if event.role == "user" and _is_nudge_text(event.text):
            outstanding = True
            continue
        if outstanding and event.role in {"assistant", "toolResult", "tool_result"}:
            if event.stop_reason and event.stop_reason.lower() in {"error", "aborted"}:
                continue
            if event.tool_call_id or event.tool_result_for or event.text.strip():
                outstanding = False
    return outstanding


def _is_nudge_text(text: str) -> bool:
    normalized = text.strip().lower()
    return bool(re.match(r"^(?:steering:\s*)?continue(?:\s*[-:]|$)", normalized))


def _has_active_tool_wait(events: list[Event]) -> bool:
    open_calls: set[str] = set()
    for event in events[-80:]:
        if event.tool_call_id:
            open_calls.add(event.tool_call_id)
        if event.tool_result_for and event.tool_result_for in open_calls:
            open_calls.remove(event.tool_result_for)
    return bool(open_calls)


def _latest_assistant_completed(events: list[Event]) -> bool:
    for event in reversed(events[-80:]):
        if event.role == "user":
            return False
        if event.role == "assistant":
            text = _event_text(event)
            if CONTEXT_RE.search(text) or RECOVERABLE_RE.search(text) or HARNESS_BLOCK_RE.search(text):
                return False
            if event.stop_reason.lower() in {"length", "error", "aborted"}:
                return False
            if event.text.strip():
                return True
        if event.role in {"toolResult", "tool_result"}:
            continue
    return False


def _latest_event(events: list[Event], role: str) -> Event | None:
    for event in reversed(events):
        if event.role == role:
            return event
    return None


def _latest_meaningful_event(events: list[Event], role: str) -> Event | None:
    for event in reversed(events):
        if event.role != role:
            continue
        if role == "user" and _is_error_echo_user(event):
            continue
        return event
    return None


def _is_error_echo_user(event: Event) -> bool:
    if event.role != "user":
        return False
    text = _event_text(event)
    return bool(
        RECOVERABLE_RE.search(text)
        or CONTEXT_RE.search(text)
        or HARNESS_BLOCK_RE.search(text)
    )


def _old_failure_cleared_by_later_progress(events: list[Event]) -> Decision | None:
    latest_failure_index: int | None = None
    for index, event in enumerate(events):
        text = _event_text(event)
        if _is_failure_event(event):
            latest_failure_index = index

    if latest_failure_index is None or latest_failure_index >= len(events) - 1:
        return None

    later = events[latest_failure_index + 1 :]
    if _has_active_tool_wait(later):
        return Decision(
            "active_tool_wait",
            False,
            "Newer tool progress exists after the older failure.",
            later[-1],
        )

    for event in reversed(later):
        if event.role == "user" and _is_error_echo_user(event):
            continue
        if event.role == "user" and not _is_nudge_text(event.text):
            return Decision(
                "awaiting_assistant",
                False,
                "Newer user turn exists after the older failure.",
                event,
            )
        if event.role in {"assistant", "toolResult", "tool_result"}:
            text = _event_text(event)
            if not _is_failure_event(event):
                return Decision(
                    "active_generation",
                    False,
                    "Newer model/tool progress exists after the older failure.",
                    event,
                )
    return None


def _is_failure_event(event: Event) -> bool:
    text = _event_text(event)
    if (
        CONTEXT_RE.search(text)
        or RECOVERABLE_RE.search(text)
        or HARNESS_BLOCK_RE.search(text)
        or NON_RECOVERABLE_TOOL_ERROR_RE.search(text)
    ):
        return True
    return event.stop_reason.lower() in {"length", "error", "aborted"}
