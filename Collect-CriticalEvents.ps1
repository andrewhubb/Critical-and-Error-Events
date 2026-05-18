#Requires -Version 5.1
<#
.SYNOPSIS
    Collects Critical and Error events from monitored servers and appends to a CSV.
.DESCRIPTION
    Reads server list from servers.txt, queries each server via Get-WinEvent (RPC)
    with a CIM/WSMAN then CIM/DCOM fallback. Results are appended to data\events.csv.
    Designed to run as a scheduled task before business hours.
.PARAMETER HoursBack
    How many hours of events to collect. Default 25 gives a comfortable overlap for daily runs.
.PARAMETER ServersFile
    Path to the tab-delimited servers file. Defaults to servers.txt alongside this script.
.PARAMETER OutputCsv
    Path for the output CSV. Defaults to data\events.csv alongside this script.
.PARAMETER LogFile
    Path for the run log. Defaults to data\collection.log alongside this script.
.EXAMPLE
    .\Collect-CriticalEvents.ps1
.EXAMPLE
    .\Collect-CriticalEvents.ps1 -HoursBack 48
#>
param(
    [int]    $HoursBack    = 25,
    [string] $ServersFile  = "$PSScriptRoot\servers.txt",
    [string] $OutputCsv    = "$PSScriptRoot\data\events.csv",
    [string] $LogFile      = "$PSScriptRoot\data\collection.log"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Log {
    param(
        [string] $Message,
        [ValidateSet('INFO','WARN','ERROR')] [string] $Level = 'INFO'
    )
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    switch ($Level) {
        'WARN'  { Write-Warning $Message }
        'ERROR' { Write-Host $line -ForegroundColor Red }
        default { Write-Host $line }
    }
}

function Truncate-Message {
    param([string] $Text, [int] $MaxLength = 500)
    if (-not $Text) { return '' }
    $cleaned = $Text -replace '[\r\n\t]+', ' ' -replace '\s{2,}', ' '
    if ($cleaned.Length -gt $MaxLength) { return $cleaned.Substring(0, $MaxLength) + '...' }
    return $cleaned
}

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
$dataDir = Split-Path $OutputCsv -Parent
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}

Write-Log "================================================================"
Write-Log "Collection started  |  HoursBack=$HoursBack  |  Servers=$ServersFile"

# ---------------------------------------------------------------------------
# Load server list  (format: <index><TAB><hostname>)
# ---------------------------------------------------------------------------
if (-not (Test-Path $ServersFile)) {
    Write-Log "Servers file not found: $ServersFile" 'ERROR'
    exit 1
}

$servers = Get-Content $ServersFile |
    Where-Object { $_ -match '\S' } |
    ForEach-Object { ($_ -split '\s+', 2)[-1].Trim() } |
    Where-Object { $_ -ne '' }

Write-Log "Loaded $($servers.Count) servers"

# ---------------------------------------------------------------------------
# Collection loop
# ---------------------------------------------------------------------------
$startTime   = (Get-Date).AddHours(-$HoursBack)
$collectedAt = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$rows        = [System.Collections.Generic.List[PSCustomObject]]::new()

foreach ($server in $servers) {
    Write-Log "--- $server ---"

    $serverRows  = [System.Collections.Generic.List[PSCustomObject]]::new()
    $connected   = $false
    $method      = 'None'
    $errorDetail = 'Unknown error'

    # ------------------------------------------------------------------
    # Method 1: Get-WinEvent -ComputerName (uses RPC/DCOM, no WinRM needed)
    # ------------------------------------------------------------------
    try {
        $filter = @{
            LogName   = @('System', 'Application')
            Level     = @(1, 2)       # 1 = Critical, 2 = Error
            StartTime = $startTime
        }
        $winEvents = Get-WinEvent -ComputerName $server -FilterHashtable $filter `
                                  -ErrorAction Stop
        $connected = $true
        $method    = 'GetWinEvent'

        foreach ($e in $winEvents) {
            $levelName = if ($e.Level -eq 1) { 'Critical' } else { 'Error' }
            $serverRows.Add([PSCustomObject]@{
                CollectedAt      = $collectedAt
                Server           = $server
                LogName          = $e.LogName
                TimeGenerated    = $e.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')
                EventId          = $e.Id
                Level            = $levelName
                LevelValue       = [int]$e.Level
                Source           = $e.ProviderName
                Message          = Truncate-Message $e.Message
                CollectionMethod = $method
            })
        }
        Write-Log "  $($serverRows.Count) event(s) via $method"
    }
    catch [System.Diagnostics.Eventing.Reader.EventLogNotFoundException] {
        Write-Log "  Log not found on $server (server may be running minimal Windows)" 'WARN'
        $connected = $true   # reachable, just no matching log
        $method    = 'GetWinEvent-NoLog'
    }
    catch {
        $errorDetail = $_.Exception.Message
        Write-Log "  Get-WinEvent failed: $errorDetail" 'WARN'
    }

    # ------------------------------------------------------------------
    # Method 2: CIM fallback (WSMAN first, then DCOM)
    # Win32_NTLogEvent EventType 1=Error (no separate Critical in this class)
    # ------------------------------------------------------------------
    if (-not $connected) {
        Write-Log "  Trying CIM fallback..."
        $cimSession = $null

        foreach ($protocol in @('Wsman', 'Dcom')) {
            try {
                $opt        = New-CimSessionOption -Protocol $protocol
                $cimSession = New-CimSession -ComputerName $server `
                                             -SessionOption $opt `
                                             -OperationTimeoutSec 30 `
                                             -ErrorAction Stop
                $method = "CIM-$protocol"
                break
            }
            catch { continue }
        }

        if ($cimSession) {
            try {
                $wmiStart  = $startTime.ToUniversalTime().ToString('yyyyMMddHHmmss.ffffff+000')
                $cimFilter = "EventType=1 AND TimeGenerated >= '$wmiStart'"

                foreach ($logName in @('System', 'Application')) {
                    $cimEvents = Get-CimInstance -CimSession $cimSession `
                                                 -ClassName Win32_NTLogEvent `
                                                 -Filter "Logfile='$logName' AND $cimFilter" `
                                                 -ErrorAction Stop
                    foreach ($e in $cimEvents) {
                        $tg = if ($e.TimeGenerated) { $e.TimeGenerated.ToString('yyyy-MM-dd HH:mm:ss') } else { $collectedAt }
                        $serverRows.Add([PSCustomObject]@{
                            CollectedAt      = $collectedAt
                            Server           = $server
                            LogName          = $e.Logfile
                            TimeGenerated    = $tg
                            EventId          = $e.EventCode
                            Level            = 'Error'
                            LevelValue       = 2
                            Source           = $e.SourceName
                            Message          = Truncate-Message $e.Message
                            CollectionMethod = $method
                        })
                    }
                }

                Remove-CimSession $cimSession
                $connected = $true
                Write-Log "  $($serverRows.Count) event(s) via $method"
            }
            catch {
                Write-Log "  CIM query failed: $($_.Exception.Message)" 'WARN'
                try { Remove-CimSession $cimSession } catch {}
            }
        }
    }

    # ------------------------------------------------------------------
    # If all methods failed, record a connection-error sentinel row
    # ------------------------------------------------------------------
    if (-not $connected) {
        Write-Log "  Cannot reach $server - recording connection error" 'ERROR'
        $rows.Add([PSCustomObject]@{
            CollectedAt      = $collectedAt
            Server           = $server
            LogName          = 'N/A'
            TimeGenerated    = $collectedAt
            EventId          = 0
            Level            = 'CONNECTION ERROR'
            LevelValue       = -1
            Source           = 'CollectionScript'
            Message          = "Failed to connect: $errorDetail"
            CollectionMethod = 'None'
        })
        continue
    }

    $rows.AddRange($serverRows)
}

# ---------------------------------------------------------------------------
# Write results to CSV
# ---------------------------------------------------------------------------
Write-Log "Writing $($rows.Count) total row(s) to $OutputCsv"

if ($rows.Count -gt 0) {
    $csvExists = Test-Path $OutputCsv
    if ($csvExists) {
        $rows | Export-Csv -Path $OutputCsv -Append -NoTypeInformation -Encoding UTF8
    }
    else {
        $rows | Export-Csv -Path $OutputCsv -NoTypeInformation -Encoding UTF8
    }
}
else {
    Write-Log "No events found in the collection window - CSV unchanged"
}

Write-Log "Collection complete"
Write-Log "================================================================"
