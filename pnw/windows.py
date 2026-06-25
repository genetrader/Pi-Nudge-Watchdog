from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NudgeResult:
    ok: bool
    summary: str


@dataclass(frozen=True)
class WindowCandidate:
    pid: int
    title: str
    process_name: str


def list_console_windows(
    window_title_regex: str = "",
    timeout_seconds: int = 25,
) -> list[WindowCandidate]:
    pattern = window_title_regex or ".*"
    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        (
            "$ErrorActionPreference='SilentlyContinue';"
            "$pattern = " + repr(pattern) + ";"
            "Get-Process | Where-Object {"
            "$_.MainWindowTitle -and "
            "($_.ProcessName -match 'cmd|WindowsTerminal|powershell|pwsh') -and "
            "($_.MainWindowTitle -match $pattern)"
            "} | Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress"
        ),
    ]
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    import json

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    candidates: list[WindowCandidate] = []
    for item in payload if isinstance(payload, list) else []:
        try:
            candidates.append(
                WindowCandidate(
                    pid=int(item.get("Id")),
                    title=str(item.get("MainWindowTitle") or ""),
                    process_name=str(item.get("ProcessName") or ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return candidates


def resolve_target_pid(
    profile: str,
    explicit_pid: int = 0,
    window_title_regex: str = "",
    allow_generic: bool = False,
) -> NudgeResult:
    if explicit_pid > 0:
        return NudgeResult(True, str(explicit_pid))

    windows = list_console_windows(window_title_regex)
    exact = [w for w in windows if profile and profile.lower() in w.title.lower()]
    if exact:
        newest = exact[0]
        return NudgeResult(True, str(newest.pid))

    if allow_generic and len(windows) == 1:
        return NudgeResult(True, str(windows[0].pid))

    if not windows:
        return NudgeResult(False, "No console windows matched.")

    titles = "; ".join(f"PID={w.pid} title={w.title!r}" for w in windows[:5])
    return NudgeResult(
        False,
        f"No exact window title match for profile {profile!r}; candidates: {titles}",
    )


def send_console_nudge(
    target_pid: int,
    text: str,
    helper_path: Path,
    dry_run: bool = False,
    input_mode: str = "console",
) -> NudgeResult:
    if target_pid <= 0:
        return NudgeResult(False, "No target PID supplied.")
    if dry_run:
        return NudgeResult(True, f"DRY RUN: would send {text!r} to PID {target_pid} via {input_mode}.")
    if input_mode.lower() in {"type", "paste"}:
        return send_keys_nudge(target_pid, text, input_mode.lower())
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper_path),
        "-TargetPid",
        str(target_pid),
        "-Text",
        text + "\r",
    ]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return NudgeResult(False, f"Console helper failed: {detail}")
    return NudgeResult(True, f"Sent {text!r} to PID {target_pid}.")


def send_keys_nudge(target_pid: int, text: str, input_mode: str) -> NudgeResult:
    escaped_text = text.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
Add-Type -ErrorAction SilentlyContinue -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class NudgeWin32 {{
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}}
"@
$target = Get-Process -Id {target_pid} -ErrorAction Stop
$null = [NudgeWin32]::ShowWindowAsync($target.MainWindowHandle, 9)
Start-Sleep -Milliseconds 250
$activated = [NudgeWin32]::SetForegroundWindow($target.MainWindowHandle)
Start-Sleep -Milliseconds 250
$ws = New-Object -ComObject WScript.Shell
if (-not $activated) {{ $activated = $ws.AppActivate($target.MainWindowTitle) }}
Start-Sleep -Milliseconds 250
if (-not $activated) {{ $activated = $ws.AppActivate([int]{target_pid}) }}
Start-Sleep -Milliseconds 250
if (-not $activated) {{ throw 'AppActivate failed' }}
"""
    if input_mode == "paste":
        script += f"""
$old = $null
$had = $false
try {{ $old = Get-Clipboard -Raw -ErrorAction Stop; $had = $true }} catch {{ }}
Set-Clipboard -Value '{escaped_text}'
Start-Sleep -Milliseconds 100
$ws.SendKeys('^v')
Start-Sleep -Milliseconds 100
$ws.SendKeys('{{ENTER}}')
Start-Sleep -Milliseconds 100
if ($had) {{ Set-Clipboard -Value $old }}
"""
    else:
        sendkeys_text = text.replace("+", "{+}").replace("^", "{^}").replace("%", "{%}").replace("~", "{~}")
        sendkeys_text = sendkeys_text.replace("(", "{(}").replace(")", "{)}")
        script += f"$ws.SendKeys('{sendkeys_text}{{ENTER}}')\n"
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return NudgeResult(False, f"SendKeys {input_mode} failed: {detail}")
    return NudgeResult(True, f"Sent {text!r} to PID {target_pid} via {input_mode}.")
