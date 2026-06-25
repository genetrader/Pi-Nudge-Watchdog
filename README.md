# Pi-Nudge-Watchdog

A tiny Windows PowerShell watchdog for long-running [Pi](https://github.com/withpi) local-model coding sessions.

When a local open-source model stalls with errors like:

- `terminated`
- `Request timed out`
- `Connection error`
- `Proxy error: <urlopen error [WinError 10060] ...>`
- `Retry failed after 3 attempts`
- `Aborted after 2 retry attempts`
- `stopReason: length` / max-output truncation

Pi-Nudge-Watchdog watches Pi's session JSONL file and nudges the running Pi console with:

```text
continue
```

It is intentionally small. This is a liveness nudge, not a correctness critic.

## Why

Long-horizon local-model coding can run for hours, then stop because a provider request timed out or the model server dropped a connection. A human usually fixes that by typing `continue`.

This script automates that boring part.

It also avoids stacking duplicate nudges. If Pi already has a queued `continue` steering message and has not produced a successful assistant/tool response after it, the watchdog will not add another one.

## Quick Start

Find your Pi launcher profile:

```powershell
Get-ChildItem "$env:USERPROFILE\.pi\agent\launcher-profiles" -Directory
```

Run the watchdog for all recent Pi profiles:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pi-nudge-watchdog.ps1
```

Or run it for one launcher profile:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pi-nudge-watchdog.ps1 `
  -ProfileName "your-pi-profile-name"
```

If your current Pi session is already stuck and you want one immediate nudge:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pi-nudge-watchdog.ps1 `
  -ProfileName "your-pi-profile-name" `
  -CatchUp `
  -Once
```

## Safer Test

Dry run proves detection without typing anything:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pi-nudge-watchdog.ps1 `
  -ProfileName "your-pi-profile-name" `
  -DryRun `
  -CatchUp `
  -Once
```

## No-Focus Input

By default, Pi-Nudge-Watchdog uses:

```powershell
-InputMode Console
```

That writes directly to the Pi console input buffer without focusing the Pi window. This helps avoid stealing your keyboard focus while you are typing somewhere else.

For reliable multi-model use, launch Pi windows with unique titles that include the launcher profile name, for example:

```bat
title pi - your-local-model-profile
```

When the watchdog is monitoring multiple profiles, it refuses to nudge a generic Pi window unless the window title matches the failed session profile. This avoids sending `continue` to the wrong local-model CLI.

For an already-open old Pi window with a generic title, you can explicitly pin a watcher to that console PID:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\pi-nudge-watchdog.ps1 `
  -ProfileName "your-pi-profile-name" `
  -TargetPid 12345
```

Fallback modes:

```powershell
-InputMode Paste
-InputMode Type
```

Those modes focus the Pi window, so use them only if console injection does not work in your terminal.

## Common Options

```powershell
-ProfileRoot "$env:USERPROFILE\.pi\agent\launcher-profiles"
-ProfileName "your-pi-profile-name"
-MaxProfiles 8
-WindowTitleRegex "Pi|Command Prompt"
-TriggerRegex "terminated|Request timed out|Connection error"
-NudgeText "continue"
-PollSeconds 10
-QuietSeconds 8
-MaxNudgesPerSession 20
-MinSecondsBetweenNudges 180
-RecentNudgeHoldSeconds 45
-DryRun
-CatchUp
-Once
```

## Notes

- Windows only.
- Designed for Pi session JSONL files.
- If Pi was launched elevated, the watchdog must also run elevated. The script requests elevation automatically unless you pass `-NoElevate`.
- Normal startup ignores failures already present in the session file. Add `-CatchUp` when you intentionally want to act on the current latest failure.
- The watchdog can monitor one profile with `-ProfileName` or the latest sessions across recent profiles with `-MaxProfiles`.
- The watchdog is single-flight by default: it will not queue a second nudge while one is already outstanding, and it persists a short recent-nudge hold file under `logs/` so restarts do not immediately stack another nudge.
- This does not detect whether the model is doing good work. It only keeps the session moving after common transient failures.

## V2 Harness-Aware Watchdog

The repo now includes an experimental v2 Python core beside the original
PowerShell script. The original script remains the stable Pi-only path.

V2 adds:

- Pi, OMP, and OpenClaude session adapters.
- Conservative OpenCode discovery, with wrapper support planned if no transcript
  is exposed.
- A generic log adapter for harnesses that only expose terminal output.
- A normalized event model instead of Pi-only JSONL assumptions.
- Context-aware classification so compaction/context failures are not blindly
  nudged.
- `doctor`, `status`, `list-sessions`, `once`, `watch`, and fixture test modes.
- Exact target binding. V2 refuses generic console windows unless you supply an
  explicit `--target-pid` or opt into generic targeting.

Read the design notes:

```powershell
Get-Content .\V2_DESIGN.md
```

Inspect what v2 can see:

```powershell
python .\watchdog.py doctor
python .\watchdog.py list-sessions --harness all
python .\watchdog.py status --harness all
```

For OpenCode installs that do not expose transcript files, start OpenCode through
the wrapper so v2 has a transcript to watch:

```powershell
.\opencode-nudge-wrapper.ps1 -ProfileName opencode-local
```

Then watch the wrapper transcript:

```powershell
python .\watchdog.py watch `
  --harness generic `
  --profile opencode-local `
  --target-pid 12345
```

Dry-run a current failure without typing:

```powershell
python .\watchdog.py once `
  --harness pi `
  --profile "your-pi-profile-name" `
  --catch-up `
  --dry-run `
  --target-pid 12345
```

Point any adapter at a custom root:

```powershell
python .\watchdog.py status --harness generic --generic-root C:\path\to\logs
```

Run the v2 watcher for a bound target:

```powershell
python .\watchdog.py watch `
  --harness pi `
  --profile "your-pi-profile-name" `
  --target-pid 12345
```

Run the fixture tests:

```powershell
python -m unittest discover -s tests -v
```

V2 intentionally treats these differently:

- `Request timed out`, `Connection error`, `terminated`, proxy WinError 10060:
  recoverable provider failures.
- `stopReason: length`: safe to continue because the model hit output length.
- queued `continue`: do not stack another nudge.
- context overflow, session too large, compaction failure, turn prefix
  summarization failure: log/refuse by default.

## License

MIT
