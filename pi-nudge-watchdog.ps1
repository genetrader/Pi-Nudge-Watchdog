param(
    [string]$ProfileRoot = "$env:USERPROFILE\.pi\agent\launcher-profiles",
    [Parameter(Mandatory=$true)]
    [string]$ProfileName,
    [string]$WindowTitleRegex = "",
    [string]$TriggerRegex = "terminated|Request timed out|Connection error|Retry failed after \d+ attempts|Aborted after \d+ retry attempts",
    [string]$NudgeText = "continue",
    [int]$PollSeconds = 10,
    [int]$QuietSeconds = 8,
    [int]$MaxNudgesPerSession = 20,
    [ValidateSet("Console", "Paste", "Type")]
    [string]$InputMode = "Console",
    [switch]$DryRun,
    [switch]$Once,
    [switch]$CatchUp,
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"

if (-not $WindowTitleRegex) {
    $WindowTitleRegex = [regex]::Escape([string][char]0x03C0)
}

Add-Type -ErrorAction SilentlyContinue -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class PiNudgeWin32 {
    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $NoElevate -and -not (Test-IsAdmin)) {
    $argsList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-ProfileRoot", "`"$ProfileRoot`"",
        "-ProfileName", "`"$ProfileName`"",
        "-WindowTitleRegex", "`"$WindowTitleRegex`"",
        "-TriggerRegex", "`"$TriggerRegex`"",
        "-NudgeText", "`"$NudgeText`"",
        "-PollSeconds", "$PollSeconds",
        "-QuietSeconds", "$QuietSeconds",
        "-MaxNudgesPerSession", "$MaxNudgesPerSession",
        "-InputMode", "$InputMode"
    )
    if ($DryRun) { $argsList += "-DryRun" }
    if ($Once) { $argsList += "-Once" }
    if ($CatchUp) { $argsList += "-CatchUp" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $argsList
    Write-Host "Pi-Nudge-Watchdog requested elevation. Use the elevated watchdog window."
    exit 0
}

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$watchLog = Join-Path $logDir ("pi-nudge-watchdog-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

function Write-WatchLog([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -LiteralPath $watchLog -Value $line
}

function Get-LatestSessionFile {
    $profile = Join-Path $ProfileRoot $ProfileName
    $sessions = Join-Path $profile "sessions"
    if (-not (Test-Path -LiteralPath $sessions)) { return $null }
    return Get-ChildItem -LiteralPath $sessions -File -Filter "*.jsonl" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}

function Get-RecentFailureKey([System.IO.FileInfo]$SessionFile) {
    $lines = Get-Content -LiteralPath $SessionFile.FullName -Tail 60 -ErrorAction SilentlyContinue
    [Array]::Reverse($lines)

    foreach ($line in $lines) {
        if ($line -notmatch $TriggerRegex) { continue }
        try {
            $obj = $line | ConvertFrom-Json -ErrorAction Stop
            $msg = $obj.message
            $err = [string]$msg.errorMessage
            $stop = [string]$msg.stopReason
            $role = [string]$msg.role
            $stamp = [string]$obj.timestamp
            if (($role -eq "assistant" -or $msg) -and ($stop -match "error|aborted" -or $err)) {
                if ($err -match $TriggerRegex -or $line -match $TriggerRegex) {
                    return "{0}|{1}|{2}|{3}" -f $SessionFile.FullName, $obj.id, $stamp, $err
                }
            }
        } catch {
            return "{0}|raw|{1}|{2}" -f $SessionFile.FullName, $SessionFile.LastWriteTimeUtc.Ticks, ($line.Substring(0, [Math]::Min(160, $line.Length)))
        }
    }
    return $null
}

function Send-NudgeToPi {
    $candidates = Get-Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.MainWindowTitle -and
            ($_.ProcessName -match "cmd|WindowsTerminal|powershell|pwsh") -and
            ($_.MainWindowTitle -match $WindowTitleRegex)
        } |
        Sort-Object @{Expression={ if ($_.MainWindowTitle -match ("^" + [regex]::Escape([string][char]0x03C0))) { 0 } else { 1 } }}, StartTime -Descending

    $target = $candidates | Select-Object -First 1
    if (-not $target) {
        Write-WatchLog "No Pi-like window matched WindowTitleRegex='$WindowTitleRegex'."
        return $false
    }

    if ($DryRun) {
        Write-WatchLog "DRY RUN: would send '$NudgeText' to PID=$($target.Id) title='$($target.MainWindowTitle)'."
        return $true
    }

    if (-not (Test-IsAdmin)) {
        Write-WatchLog "Refusing to send input from non-elevated watchdog. Relaunch elevated when Pi is elevated."
        return $false
    }

    if ($InputMode -eq "Console") {
        $helper = Join-Path $PSScriptRoot "pi-console-input-helper.ps1"
        $text = $NudgeText + "`r"
        $proc = Start-Process powershell.exe -WindowStyle Hidden -Wait -PassThru -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", "`"$helper`"",
            "-TargetPid", "$($target.Id)",
            "-Text", "`"$text`""
        )
        if ($proc.ExitCode -ne 0) {
            Write-WatchLog "Console input helper failed with exit code $($proc.ExitCode) for PID=$($target.Id)."
            return $false
        }
        Write-WatchLog "Wrote '$NudgeText' to console input buffer for PID=$($target.Id) title='$($target.MainWindowTitle)' without focusing window."
        return $true
    }

    [PiNudgeWin32]::ShowWindowAsync($target.MainWindowHandle, 9) | Out-Null
    Start-Sleep -Milliseconds 350
    $activated = [PiNudgeWin32]::SetForegroundWindow($target.MainWindowHandle)
    Start-Sleep -Milliseconds 350

    $shell = New-Object -ComObject WScript.Shell
    if (-not $activated) {
        $activated = $shell.AppActivate($target.MainWindowTitle)
        Start-Sleep -Milliseconds 350
    }
    if (-not $activated) {
        $activated = $shell.AppActivate([int]$target.Id)
        Start-Sleep -Milliseconds 350
    }

    if ($InputMode -eq "Paste") {
        $previousClipboard = $null
        $hadClipboard = $false
        try {
            $previousClipboard = Get-Clipboard -Raw -ErrorAction Stop
            $hadClipboard = $true
        } catch {
            $hadClipboard = $false
        }
        Set-Clipboard -Value $NudgeText
        Start-Sleep -Milliseconds 150
        $shell.SendKeys("^v")
        Start-Sleep -Milliseconds 150
        $shell.SendKeys("{ENTER}")
        Start-Sleep -Milliseconds 150
        if ($hadClipboard) { Set-Clipboard -Value $previousClipboard }
    } else {
        $shell.SendKeys($NudgeText + "{ENTER}")
    }
    Write-WatchLog "Sent '$NudgeText' to PID=$($target.Id) title='$($target.MainWindowTitle)' activated=$activated inputMode=$InputMode."
    return $true
}

$handled = @{}
$nudgeCounts = @{}

Write-WatchLog "Pi-Nudge-Watchdog started. ProfileName='$ProfileName' WindowTitleRegex='$WindowTitleRegex' PollSeconds=$PollSeconds DryRun=$DryRun Once=$Once CatchUp=$CatchUp Elevated=$(Test-IsAdmin) InputMode=$InputMode"

if (-not $CatchUp) {
    $startupSession = Get-LatestSessionFile
    if ($startupSession) {
        $startupKey = Get-RecentFailureKey -SessionFile $startupSession
        if ($startupKey) {
            $handled[$startupKey] = $true
            Write-WatchLog "Ignoring pre-existing failure at startup. Use -CatchUp to act on the current latest failure."
        }
    }
}

while ($true) {
    try {
        $session = Get-LatestSessionFile
        if (-not $session) {
            Write-WatchLog "No Pi session file found yet."
            Start-Sleep -Seconds $PollSeconds
            continue
        }

        $key = Get-RecentFailureKey -SessionFile $session
        if ($key -and -not $handled.ContainsKey($key)) {
            $sessionKey = $session.FullName
            if (-not $nudgeCounts.ContainsKey($sessionKey)) { $nudgeCounts[$sessionKey] = 0 }

            if ($nudgeCounts[$sessionKey] -ge $MaxNudgesPerSession) {
                Write-WatchLog "Max nudges reached for '$sessionKey'. Not sending more."
                $handled[$key] = $true
            } else {
                Write-WatchLog "Detected recoverable Pi failure in '$($session.Name)': $key"
                Start-Sleep -Seconds $QuietSeconds
                if (Send-NudgeToPi) {
                    $nudgeCounts[$sessionKey]++
                    $handled[$key] = $true
                    Write-WatchLog "Nudge count for this session: $($nudgeCounts[$sessionKey])/$MaxNudgesPerSession"
                }
            }
        }
    } catch {
        Write-WatchLog "Watchdog error: $($_.Exception.Message)"
    }
    if ($Once) { break }
    Start-Sleep -Seconds $PollSeconds
}
