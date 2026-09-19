param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$TaskName = 'NgheTinhDashboard-DailyBackup',
    [string]$At = '08:00'
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $ProjectRoot).Path
$destination = Join-Path $project 'backend\.data\backups\automatic'
$runner = Join-Path $project 'backend\backup_job.py'
$python = Join-Path $project '.venv-audit\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Missing .venv-audit Python runtime.' }
if (-not (Test-Path -LiteralPath $runner)) { throw 'Missing backup runner.' }
$npx = (Get-Command npx.cmd -ErrorAction Stop).Source
$null = New-Item -ItemType Directory -Force -Path $destination
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$userSid = $identity.User.Value
# Only the current Windows account, SYSTEM and local administrators may read backups.
& icacls.exe $destination /inheritance:r /grant:r "*${userSid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Cannot secure backup directory.' }
$configPath = Join-Path $destination 'job-config.json'
$config = [ordered]@{
    destination = $destination
    npx_path = $npx
    project = '55073ec0-5864-4101-9847-30c50e82a60f'
    service = 'cfe20209-6378-492c-bf7e-fee603ae0f62'
    environment = 'f1e98916-eda9-4450-85aa-52d70a43f435'
    keep = 30
    schedule_enabled = $false
}
$config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
$action = New-ScheduledTaskAction -Execute $python -Argument "-B `"$runner`" --config `"$configPath`"" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$principal = New-ScheduledTaskPrincipal -UserId $identity.Name -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15)
$description = 'Daily verified SQLite online backup from Railway. Runs while this Windows account is signed in; catches up after a missed schedule. Keeps 30 managed copies; no database restore.'
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Description -ne $description) { throw 'Task name already exists and is not owned by this installer.' }
$null = Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $description -Force
$task = Get-ScheduledTask -TaskName $TaskName
if ($task.State -eq 'Disabled') { throw 'Task is disabled.' }
$config.schedule_enabled = $true
$config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
[pscustomobject]@{ TaskName = $TaskName; State = $task.State; DailyAt = $At; Destination = $destination; RequiresSignedInUser = $true }
