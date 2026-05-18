#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Registers the Critical & Error Events collection script as a daily scheduled task.
.DESCRIPTION
    Creates a Task Scheduler task that runs Collect-CriticalEvents.ps1 at 08:00 every day.
    Requires administrator privileges. Run once on the server that will perform collection.
.PARAMETER TaskName
    Name for the scheduled task. Default: CriticalErrorEventsCollector
.PARAMETER RunAsUser
    The account to run the task as. Default: SYSTEM (runs without a logged-in session).
    Use "DOMAIN\ServiceAccount" for a specific service account.
.PARAMETER RunTime
    Daily trigger time. Default: 08:00.
.EXAMPLE
    .\Register-ScheduledTask.ps1
.EXAMPLE
    .\Register-ScheduledTask.ps1 -RunAsUser "CORP\svc-monitoring" -RunTime "07:45"
#>
param(
    [string] $TaskName  = 'CriticalErrorEventsCollector',
    [string] $RunAsUser = 'SYSTEM',
    [string] $RunTime   = '08:00'
)

$scriptPath    = Join-Path $PSScriptRoot 'Collect-CriticalEvents.ps1'
$workingDir    = $PSScriptRoot

if (-not (Test-Path $scriptPath)) {
    Write-Error "Collection script not found: $scriptPath"
    exit 1
}

Write-Host "Registering scheduled task: $TaskName"
Write-Host "  Script   : $scriptPath"
Write-Host "  Run as   : $RunAsUser"
Write-Host "  Trigger  : Daily at $RunTime"

$action = New-ScheduledTaskAction `
    -Execute    'powershell.exe' `
    -Argument   "-NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$scriptPath`"" `
    -WorkingDirectory $workingDir

$trigger = New-ScheduledTaskTrigger -Daily -At $RunTime

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit      (New-TimeSpan -Hours 2) `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -MultipleInstances       IgnoreNew `
    -Priority                5

$principal = if ($RunAsUser -eq 'SYSTEM') {
    New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
} else {
    New-ScheduledTaskPrincipal -UserId $RunAsUser -LogonType Password -RunLevel Highest
}

$params = @{
    TaskName    = $TaskName
    Action      = $action
    Trigger     = $trigger
    Settings    = $settings
    Principal   = $principal
    Description = 'Collects Critical and Error events from monitored servers and appends to data\events.csv'
    Force       = $true
}

if ($RunAsUser -ne 'SYSTEM') {
    $cred = Get-Credential -UserName $RunAsUser -Message "Enter password for $RunAsUser"
    $params['Password'] = $cred.GetNetworkCredential().Password
}

Register-ScheduledTask @params | Out-Null

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Task registered successfully." -ForegroundColor Green
    Write-Host ""
    Write-Host "Useful commands:"
    Write-Host "  Run now   : Start-ScheduledTask -TaskName '$TaskName'"
    Write-Host "  Check     : Get-ScheduledTaskInfo -TaskName '$TaskName'"
    Write-Host "  Remove    : Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
} else {
    Write-Error "Task registration failed."
}
