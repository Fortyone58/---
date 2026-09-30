param([switch]$Force)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Force) {
    Write-Host 'This restores the SIMULATED demo data. A backup will be saved in work\backups.' -ForegroundColor Yellow
    if ((Read-Host 'Type RESET to continue') -ne 'RESET') { Write-Host 'Cancelled.'; exit 0 }
}
& (Join-Path $PSScriptRoot 'stop.ps1')
foreach ($port in @(5173,8000)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is still active. Stop the running service before resetting." }
}
Push-Location (Join-Path $projectRoot 'backend')
try {
    & '.\.venv\Scripts\python.exe' -m app.seed --reset --confirm campus-demo
    if ($LASTEXITCODE -ne 0) { throw 'Reset was refused or failed.' }
} finally { Pop-Location }
Write-Host 'Demo restored. Run the start launcher to continue.' -ForegroundColor Green
