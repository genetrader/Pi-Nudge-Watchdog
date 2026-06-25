param(
    [string]$ProfileName = "opencode",
    [string]$LogRoot = "$env:USERPROFILE\.pi-nudge-watchdog\wrapped",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$OpenCodeArgs
)

$ErrorActionPreference = "Stop"

$safeProfile = $ProfileName -replace '[^A-Za-z0-9_.-]', '_'
if (-not $safeProfile) { $safeProfile = "opencode" }

$sessionDir = Join-Path $LogRoot $safeProfile
New-Item -ItemType Directory -Force -Path $sessionDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $sessionDir ("opencode-{0}.log" -f $stamp)

Write-Host "Pi-Nudge-Watchdog wrapper logging OpenCode output to:"
Write-Host "  $logPath"
Write-Host ""
Write-Host "In another elevated terminal, watch this transcript with:"
Write-Host "  python watchdog.py watch --harness generic --profile $safeProfile --target-pid <this-window-pid>"
Write-Host ""

try {
    Start-Transcript -LiteralPath $logPath -Append | Out-Null
    & opencode @OpenCodeArgs
    exit $LASTEXITCODE
} finally {
    try {
        Stop-Transcript | Out-Null
    } catch {
    }
}
