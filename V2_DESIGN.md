# Pi-Nudge-Watchdog v2 Design

This document captures the proposed upgrade path from a Pi-specific nudge script
to a harness-aware watchdog for long-running local/open-weight model sessions.

## Goal

Keep coding agents moving after recoverable local-model failures without
nudging the wrong terminal, stacking `continue` messages, or blindly continuing
when the real issue is context overflow or compaction failure. Context failures
use a targeted recovery nudge instead of a plain retry.

The watchdog should eventually support:

- Pi
- OMP
- OpenClaude
- OpenCode
- Other harnesses with a session log, transcript file, database, or wrapper

The watchdog should be model-agnostic. It should not care whether the backend is
MiniMax, DeepSeek, Qwen, GLM, AgentWorld, StepFun, or another open-weight model.
It watches the harness/session state and provider failure symptoms.

## Current State

The current PowerShell script is useful and should remain available as the
legacy/simple path:

- Watches Pi JSONL sessions under `.pi\agent\launcher-profiles`.
- Detects common provider failures such as timeouts, connection errors, proxy
  errors, retry exhaustion, and `stopReason: length`.
- Checks whether a `continue` is already outstanding.
- Uses a Windows console input helper to write `continue` without stealing
  focus.
- Refuses to nudge a generic Pi window when the failed profile cannot be matched
  to the window title.

Main limitations:

- Pi-specific JSONL assumptions.
- Regex-first classification.
- No normalized model/session health state.
- No first-class support for OMP, OpenClaude, or OpenCode.
- Context/compaction failures need safer handling than blind `continue`.
- No `doctor` or `status` command explaining what the watchdog sees.

## Architecture Recommendation

Recommended path: Python watchdog core plus the existing PowerShell console
input helper.

PowerShell should remain the Windows actuator because it already handles console
input and elevation. Python should own the parsing, policy, state, and CLI.

```text
watchdog.py
  adapters/
    pi.py
    omp.py
    openclaude.py
    opencode.py
  classifiers/
    recovery.py
  actuators/
    windows_console.ps1
  state/
    watchdog-state.json
  fixtures/
    pi-timeout.jsonl
    pi-length.jsonl
    omp-timeout.jsonl
    openclaude-api-error.jsonl
```

## Normalized Event Model

Every harness adapter should emit the same logical event shape:

```json
{
  "harness": "pi",
  "profile": "local-model-profile",
  "session_path": "%USERPROFILE%/.pi/agent/launcher-profiles/.../session.jsonl",
  "event_id": "stable-event-key",
  "timestamp": "2026-06-24T21:00:00Z",
  "role": "assistant",
  "text": "Proxy error: timed out",
  "stop_reason": "error",
  "error_text": "Proxy error: timed out",
  "model": "local-model-name",
  "provider": "local-provider-name",
  "tool_call_id": null,
  "tool_result_for": null,
  "usage": {
    "input_tokens": null,
    "output_tokens": null,
    "context_tokens": null,
    "context_limit": null
  }
}
```

## Recovery Classes

The classifier should return one of these decisions:

- `recoverable_provider_failure`: send a normal nudge after quiet time.
- `max_output_truncation`: send a nudge; the model likely stopped at output
  length, not because the task is done.
- `queued_nudge_exists`: do nothing.
- `active_tool_wait`: do nothing; a tool call is outstanding or just returned.
- `active_generation`: do nothing; the transcript is still moving.
- `context_or_compaction_failure`: send a targeted recovery nudge that asks the
  model to reduce requested output, summarize/compact, and continue smaller.
- `wrong_target_or_unbound_window`: refuse to act.
- `unknown`: log only unless configured otherwise.

## Context Safety Rules

These rules prevent overzealous context behavior:

1. Do not treat large context usage as a reason to nudge.
2. Do not blindly retry when the last error is a compaction or context-limit
   failure.
3. Do not keep nudging when the harness says the session is too large to safely
   continue; send one targeted recovery nudge and then rely on duplicate-nudge
   guards.
4. For `stopReason: length`, nudge is allowed because the model likely hit an
   output cap.
5. For context overflow, the default nudge asks the model to reduce requested
   output, summarize or compact prior work, and continue with a smaller
   response.
6. Do not nudge while a tool call has no matching tool result unless it has been
   stale longer than a configured threshold.
7. Do not nudge while the session file is actively changing.

## Target Binding Rules

The watchdog must prove the target before sending input:

- Prefer exact profile-to-window title match.
- Accept explicit `--target-pid` only when supplied by the user or launcher.
- Prefer launcher-written PID lockfiles for new sessions.
- Refuse generic titles such as `pi - system32` when multiple candidates exist.
- Log the refused target and the reason.

## CLI Commands

Proposed commands:

```powershell
python watchdog.py watch
python watchdog.py watch --harness pi --profile NAME
python watchdog.py watch --harness all --max-profiles 8
python watchdog.py once --catch-up --dry-run
python watchdog.py doctor
python watchdog.py status
python watchdog.py list-sessions
python watchdog.py bind-window --profile NAME --pid 12345
python watchdog.py test-fixture fixtures/pi-timeout.jsonl
```

## Harness Adapter Notes

### Pi

Pi has the best current support. It writes JSONL session files under:

```text
%USERPROFILE%\.pi\agent\launcher-profiles\<profile>\sessions
```

The current PowerShell script can be used as the reference behavior.

### OMP

OMP has launcher profiles and JSONL sessions under:

```text
%USERPROFILE%\.omp\agent\launcher-profiles\<profile>\sessions
```

Its event shape is close enough to Pi that it can share most of the JSONL
adapter with schema mapping.

### OpenClaude

OpenClaude writes JSONL project transcripts under:

```text
%USERPROFILE%\.openclaude\projects
```

It can emit `system/api_error` events and synthetic assistant messages after
retry exhaustion. The adapter should treat retry exhaustion/provider connection
failures as recoverable only if target binding is exact.

### OpenCode

OpenCode needs a live-run inspection before full adapter support. If it does
not expose a reliable transcript, v2 should support a wrapper mode that captures
stdout/stderr into a normalized event log.

The first implementation includes that wrapper fallback:

```powershell
.\opencode-nudge-wrapper.ps1 -ProfileName opencode-local
python .\watchdog.py watch --harness generic --profile opencode-local --target-pid 12345
```

This is intentionally described as generic transcript support, not native
OpenCode state support. If a future OpenCode version exposes durable structured
sessions, the `opencode` adapter should parse those directly.

## Implementation Options

### Option A: PowerShell v2

Fastest path. Extend the existing script with more roots and schema checks.

Pros:

- Smallest change.
- Easy to keep current Pi behavior.

Cons:

- Harder to maintain.
- Adapter logic and policy logic will become tangled.
- More fragile as harness support grows.

### Option B: Python Core + PowerShell Actuator

Recommended path.

Pros:

- Clean adapter architecture.
- Testable classifier.
- Easier status/doctor/dry-run commands.
- Keeps proven Windows console input behavior.

Cons:

- More initial work than patching the existing script.

### Option C: Full Supervisor With Second-Model Critic

Adds a second model that reviews progress and nudges for drift.

Pros:

- Could help with long-horizon plan drift.

Cons:

- Adds another model connection.
- More timeout surface area.
- More expensive and complex.
- Should come after the deterministic watchdog is reliable.

## First Build Milestone

The first v2 build should prove:

- Pi adapter parity with the current script.
- OMP adapter can detect the same failure classes.
- OpenClaude adapter can detect retry exhaustion/API connection failure.
- `doctor` lists discovered sessions, profiles, window candidates, and refusal
  reasons.
- `dry-run --catch-up` explains exactly what would happen.
- No duplicate nudge is sent when `continue` is already outstanding.
- Generic or ambiguous windows are refused.
- Context/compaction failures get a targeted recovery nudge instead of a blind
  retry.

## Test Fixtures

Capture small sanitized fixtures from real sessions:

- Pi proxy timeout.
- Pi WinError 10060.
- Pi `stopReason: length`.
- Pi queued `continue`.
- OMP timeout with malformed tool-call output.
- OpenClaude retry exhaustion.
- Context overflow or compaction failure.

Each fixture should assert:

- detected harness
- latest event key
- classifier result
- whether a nudge is allowed
- target binding requirement
- explanation string
