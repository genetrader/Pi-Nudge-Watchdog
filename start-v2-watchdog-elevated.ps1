param(
    [string]$Harness = "pi",
    [Parameter(Mandatory = $true)]
    [string]$Profile,
    [Parameter(Mandatory = $true)]
    [int]$TargetPid,
    [int]$PollSeconds = 10,
    [int]$QuietSeconds = 8,
    [int]$RecentNudgeHoldSeconds = 45,
    [int]$ConfirmSessionWriteSeconds = 5,
    [ValidateSet("console", "type", "paste", "auto")]
    [string]$InputMode = "auto",
    [switch]$CatchUp,
    [switch]$Once,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSCommandPath
$safeProfile = ($Profile.Trim() -replace '[^A-Za-z0-9_.-]', '_')
if (-not $safeProfile) { $safeProfile = "watchdog" }

$statePath = Join-Path $repo "logs\v2-$safeProfile-state.json"
$logPath = Join-Path $repo "logs\v2-$safeProfile.log"

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$watchdogArgs = @(
    "watchdog.py",
    $(if ($Once) { "once" } else { "watch" }),
    "--harness", $Harness,
    "--profile", $Profile.Trim(),
    "--target-pid", "$TargetPid",
    "--input-mode", $InputMode,
    "--recent-nudge-hold-seconds", "$RecentNudgeHoldSeconds",
    "--confirm-session-write-seconds", "$ConfirmSessionWriteSeconds",
    "--state-path", $statePath,
    "--log-path", $logPath
)
if (-not $Once) {
    $watchdogArgs += @("--poll-seconds", "$PollSeconds", "--quiet-seconds", "$QuietSeconds")
}
if ($CatchUp) { $watchdogArgs += "--catch-up" }
if ($DryRun) { $watchdogArgs += "--dry-run" }

if (-not (Test-IsAdmin)) {
    $quoted = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-Harness", "`"$Harness`"",
        "-Profile", "`"$($Profile.Trim())`"",
        "-TargetPid", "$TargetPid",
        "-InputMode", "`"$InputMode`"",
        "-PollSeconds", "$PollSeconds",
        "-QuietSeconds", "$QuietSeconds",
        "-RecentNudgeHoldSeconds", "$RecentNudgeHoldSeconds",
        "-ConfirmSessionWriteSeconds", "$ConfirmSessionWriteSeconds"
    )
    if ($CatchUp) { $quoted += "-CatchUp" }
    if ($Once) { $quoted += "-Once" }
    if ($DryRun) { $quoted += "-DryRun" }
    Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -WorkingDirectory $repo -ArgumentList $quoted
    Write-Host "Requested elevated v2 watchdog. Log: $logPath"
    exit 0
}

Set-Location $repo
python @watchdogArgs
