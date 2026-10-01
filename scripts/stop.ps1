$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'process-utils.ps1')
$manifest = Join-Path $projectRoot 'work\runtime\services.json'
if (Test-Path -LiteralPath $manifest) {
    $data = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
    if ($data.root -ne $projectRoot) { throw 'Service manifest belongs to another project.' }
    foreach ($item in $data.processes) {
        $proc = Get-Process -Id $item.pid -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        if ($proc.StartTime.ToUniversalTime().Ticks.ToString() -ne $item.startTicks) { Write-Warning 'PID was reused. Skipped.'; continue }
        $signature = if ($item.port -eq 8000) { 'app.main:app' } else { 'vite' }
        if (-not (Test-QingheProcess $item.pid $signature $projectRoot)) { throw 'Process identity mismatch. Refused to stop it; service manifest retained.' }
        Stop-Process -Id $item.pid
        Write-Host "Stopped Qinghe service on port $($item.port)."
    }
    Remove-Item -LiteralPath $manifest
} else { Write-Host 'No tracked web services.' }
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $python) {
    & $python (Join-Path $PSScriptRoot 'mysql_runtime.py') stop
    if ($LASTEXITCODE -ne 0) { throw 'Managed MySQL stop was refused or failed. No unrelated instance was stopped.' }
}
