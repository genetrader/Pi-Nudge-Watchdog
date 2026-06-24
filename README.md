# Pi-Nudge-Watchdog

A tiny Windows PowerShell watchdog for long-running [Pi](https://github.com/withpi) local-model coding sessions.

When a local open-source model stalls with errors like:

- `terminated`
- `Request timed out`
- `Connection error`
- `Retry failed after 3 attempts`
- `Aborted after 2 retry attempts`

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
-RecentNudgeHoldSeconds 600
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
- The watchdog is single-flight by default: it will not queue a second nudge while one is already outstanding, and it persists a recent-nudge hold file under `logs/` so restarts do not immediately stack another nudge.
- This does not detect whether the model is doing good work. It only keeps the session moving after common transient failures.

## License

MIT
