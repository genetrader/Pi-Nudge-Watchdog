from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .adapters import HarnessAdapter, make_adapters
from .classifier import Decision, classify
from .state import WatchState
from .windows import list_console_windows, resolve_target_pid, send_console_nudge


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = REPO_ROOT / "logs" / "v2-state.json"
DEFAULT_HELPER = REPO_ROOT / "pi-console-input-helper.ps1"
DEFAULT_NUDGE_TEXT = "continue"
LOOP_GUARD_NUDGE = (
    "stay on the current task; if the same tool/error repeats, stop that approach "
    "and choose another method or summarize the blocker"
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "list-sessions":
            return cmd_list_sessions(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "once":
            return cmd_once(args)
        if args.command == "watch":
            return cmd_watch(args)
        if args.command == "test-fixture":
            return cmd_test_fixture(args)
    except KeyboardInterrupt:
        print("Stopped.")
        return 130
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harness-aware Pi-Nudge-Watchdog v2")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("doctor", "list-sessions", "status", "once", "watch"):
        p = sub.add_parser(name)
        add_common_args(p)
        if name in {"once", "watch"}:
            p.add_argument("--target-pid", type=int, default=0)
            p.add_argument("--window-title-regex", default=r"^(?:pi -|p|π|OMP|OpenClaude|OpenCode)")
            p.add_argument("--allow-generic-window", action="store_true")
            p.add_argument("--input-mode", choices=["console", "type", "paste", "auto"], default="console")
            p.add_argument("--nudge-text", default=DEFAULT_NUDGE_TEXT)
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--state-path", type=Path, default=DEFAULT_STATE)
            p.add_argument("--log-path", type=Path)
            p.add_argument("--helper-path", type=Path, default=DEFAULT_HELPER)
            p.add_argument("--recent-nudge-hold-seconds", type=int, default=45)
            p.add_argument("--confirm-session-write-seconds", type=int, default=5)
            p.add_argument("--catch-up", action="store_true")
        if name == "watch":
            p.add_argument("--poll-seconds", type=int, default=10)
            p.add_argument("--quiet-seconds", type=int, default=8)

    p = sub.add_parser("test-fixture")
    p.add_argument("fixture", type=Path)
    p.add_argument(
        "--harness",
        default="pi",
        choices=["pi", "omp", "openclaude", "opencode", "generic"],
    )
    p.add_argument("--expect-kind", default="")
    p.add_argument("--expect-allow", choices=["true", "false", ""], default="")
    return parser


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--harness",
        default="all",
        choices=["all", "pi", "omp", "openclaude", "opencode", "generic"],
    )
    parser.add_argument("--profile", default="")
    parser.add_argument("--max-profiles", type=int, default=8)
    parser.add_argument("--tail", type=int, default=160)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--pi-root", type=Path)
    parser.add_argument("--omp-root", type=Path)
    parser.add_argument("--openclaude-root", type=Path)
    parser.add_argument("--opencode-root", type=Path)
    parser.add_argument("--generic-root", type=Path)


def adapters_for(args: argparse.Namespace) -> list[HarnessAdapter]:
    args.profile = args.profile.strip()
    roots = {
        name: value
        for name, value in {
            "pi": args.pi_root,
            "omp": args.omp_root,
            "openclaude": args.openclaude_root,
            "opencode": args.opencode_root,
            "generic": args.generic_root,
        }.items()
        if value
    }
    return make_adapters(
        args.harness,
        profile=args.profile,
        roots=roots,
        max_profiles=args.max_profiles,
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    rows = []
    for adapter in adapters_for(args):
        sessions = adapter.discover_sessions()
        rows.append(
            {
                "harness": adapter.harness,
                "root": str(adapter.root),
                "root_exists": adapter.root.exists(),
                "sessions": len(sessions),
                "latest": str(sessions[0].path) if sessions else "",
            }
        )
    for window in list_console_windows(timeout_seconds=25):
        rows.append(
            {
                "harness": "window",
                "root": "",
                "root_exists": True,
                "sessions": "",
                "latest": f"PID={window.pid} {window.process_name} {window.title}",
            }
        )
    emit(rows, args.json_output)
    return 0


def cmd_list_sessions(args: argparse.Namespace) -> int:
    rows = []
    for adapter in adapters_for(args):
        for session in adapter.discover_sessions():
            rows.append(
                {
                    "harness": session.harness,
                    "profile": session.profile,
                    "path": str(session.path),
                    "last_write": session.last_write,
                }
            )
    emit(rows, args.json_output)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    rows = []
    for adapter in adapters_for(args):
        for session in adapter.discover_sessions():
            events = adapter.latest_events(session, tail=args.tail)
            decision = classify(events, session)
            rows.append(_decision_row(session, decision))
    emit(rows, args.json_output)
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    return evaluate_and_maybe_nudge(args, once=True)


def cmd_watch(args: argparse.Namespace) -> int:
    if not args.catch_up:
        state = WatchState.load(args.state_path)
        for adapter in adapters_for(args):
            for session in adapter.discover_sessions():
                events = adapter.latest_events(session, tail=args.tail)
                decision = classify(events, session)
                if decision.allow_nudge and decision.event:
                    state.handled.add(decision.event.stable_key)
        state.save(args.state_path)
    while True:
        evaluate_and_maybe_nudge(args, once=False)
        time.sleep(args.poll_seconds)


def evaluate_and_maybe_nudge(args: argparse.Namespace, once: bool) -> int:
    state = WatchState.load(args.state_path)
    now = time.time()
    rows = []
    for adapter in adapters_for(args):
        for session in adapter.discover_sessions():
            events = adapter.latest_events(session, tail=args.tail)
            decision = classify(events, session, now=now)
            key = decision.event.stable_key if decision.event else str(session.path)
            row = _decision_row(session, decision)
            if decision.allow_nudge:
                if once and not args.catch_up and key not in state.handled:
                    row["action"] = "ignored_existing_failure_without_catch_up"
                    state.handled.add(key)
                elif key in state.handled:
                    row["action"] = "already_handled"
                elif now - state.last_nudge_at < args.recent_nudge_hold_seconds:
                    row["action"] = "recent_nudge_hold"
                else:
                    target = resolve_target_pid(
                        session.profile,
                        explicit_pid=args.target_pid,
                        window_title_regex=args.window_title_regex,
                        allow_generic=args.allow_generic_window,
                    )
                    if not target.ok:
                        row["action"] = f"refused_target_binding: {target.summary}"
                        rows.append(row)
                        continue
                    target_pid = int(target.summary)
                    if not once and getattr(args, "quiet_seconds", 0):
                        time.sleep(args.quiet_seconds)
                    result = send_and_confirm(
                        session=session,
                        target_pid=target_pid,
                        text=_nudge_text_for_decision(decision, args.nudge_text),
                        helper_path=args.helper_path,
                        dry_run=args.dry_run,
                        input_mode=args.input_mode,
                        confirm_seconds=args.confirm_session_write_seconds,
                    )
                    row["action"] = result.summary
                    state.last_nudge_at = time.time()
                    if not args.dry_run:
                        state.handled.add(key)
            else:
                row["action"] = "none"
                if decision.event and decision.kind in {
                    "context_or_compaction_failure",
                    "max_output_truncation",
                    "recoverable_provider_failure",
                    "queued_nudge_exists",
                    "tool_or_code_error",
                }:
                    state.handled.add(key)
            rows.append(row)
    state.save(args.state_path)
    emit(rows, args.json_output)
    write_log_rows(args.log_path, rows)
    return 0


def send_and_confirm(
    session,
    target_pid: int,
    text: str,
    helper_path: Path,
    dry_run: bool,
    input_mode: str,
    confirm_seconds: int,
):
    modes = ["console", "paste", "type"] if input_mode == "auto" else [input_mode]
    attempts: list[str] = []
    before = session.path.stat().st_mtime if session.path.exists() else session.last_write
    for mode in modes:
        result = send_console_nudge(
            target_pid,
            text,
            helper_path,
            dry_run=dry_run,
            input_mode=mode,
        )
        if not result.ok or dry_run or confirm_seconds <= 0:
            if result.ok:
                return result
            attempts.append(result.summary)
            continue

        deadline = time.time() + confirm_seconds
        while time.time() < deadline:
            try:
                if session.path.stat().st_mtime > before:
                    if attempts:
                        return type(result)(True, f"{result.summary} Previous attempts: {' | '.join(attempts)}")
                    return result
            except OSError:
                break
            time.sleep(0.25)
        attempts.append(f"{result.summary} but Pi session did not record new input within {confirm_seconds}s")

    return type(result)(False, "Input delivery failed: " + " | ".join(attempts))


def _nudge_text_for_decision(decision: Decision, configured_text: str) -> str:
    if configured_text != DEFAULT_NUDGE_TEXT:
        return configured_text
    if decision.kind == "context_or_compaction_failure":
        return (
            f"{DEFAULT_NUDGE_TEXT} - recover from the context/output-token limit by reducing requested output, "
            "summarizing or compacting the prior work, and continuing with a smaller response; "
            f"{LOOP_GUARD_NUDGE}"
        )
    if decision.kind == "malformed_tool_call_blocked":
        return (
            f"{DEFAULT_NUDGE_TEXT} - retry with one smaller valid supported tool call only; "
            f"{LOOP_GUARD_NUDGE}"
        )
    return f"{DEFAULT_NUDGE_TEXT} - {LOOP_GUARD_NUDGE}"


def cmd_test_fixture(args: argparse.Namespace) -> int:
    from .adapters import GenericLogAdapter, OmpAdapter, OpenClaudeAdapter, OpenCodeAdapter, PiAdapter
    from .events import SessionRef

    classes = {
        "pi": PiAdapter,
        "omp": OmpAdapter,
        "openclaude": OpenClaudeAdapter,
        "opencode": OpenCodeAdapter,
        "generic": GenericLogAdapter,
    }
    adapter = classes[args.harness](root=args.fixture.parent)
    session = SessionRef(args.harness, "fixture", args.fixture, args.fixture.stat().st_mtime)
    events = adapter.latest_events(session, tail=300)
    decision = classify(events, session, now=time.time() + 1000)
    row = _decision_row(session, decision)
    emit([row], True)
    if args.expect_kind and decision.kind != args.expect_kind:
        print(f"Expected kind {args.expect_kind!r}, got {decision.kind!r}", file=sys.stderr)
        return 1
    if args.expect_allow:
        expected = args.expect_allow == "true"
        if decision.allow_nudge != expected:
            print(f"Expected allow {expected}, got {decision.allow_nudge}", file=sys.stderr)
            return 1
    return 0


def _decision_row(session, decision) -> dict[str, object]:
    return {
        "harness": session.harness,
        "profile": session.profile,
        "session": str(session.path),
        "decision": decision.kind,
        "allow_nudge": decision.allow_nudge,
        "reason": decision.reason,
        "event_key": decision.event.stable_key if decision.event else "",
    }


def emit(rows: list[dict[str, object]], json_output: bool) -> None:
    if json_output:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No rows.")
        return
    for row in rows:
        if "decision" in row:
            print(
                f"{row.get('harness')} {row.get('profile')} "
                f"{row.get('decision', '')} allow={row.get('allow_nudge', '')} "
                f"{row.get('reason', '')}"
            )
        else:
            print(" ".join(str(row.get(key, "")) for key in row.keys()))
        if row.get("session"):
            print(f"  session: {row['session']}")
        if row.get("action"):
            print(f"  action: {row['action']}")


def write_log_rows(path: Path | None, rows: list[dict[str, object]]) -> None:
    if not path:
        return
    import json
    from datetime import datetime

    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({"time": stamp, **row}, ensure_ascii=False) + "\n")
