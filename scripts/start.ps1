param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'process-utils.ps1')
$backend = Join-Path $projectRoot 'backend'
$frontend = Join-Path $projectRoot 'frontend'
$runtime = Join-Path $projectRoot 'work\runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

function Read-Health {
    try { return Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/ping' -TimeoutSec 2 } catch { return $null }
}
function Port-Owner([int]$Port) {
    return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

try {
    $python = Join-Path $backend '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        Push-Location $backend
        try { & uv sync --frozen; if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' } } finally { Pop-Location }
    }
    & $python (Join-Path $PSScriptRoot 'mysql_runtime.py') start
    if ($LASTEXITCODE -ne 0) { throw 'Managed MySQL startup failed. The existing MySQL service was not changed.' }
    Push-Location $backend
    try { & $python -m app.seed; if ($LASTEXITCODE -ne 0) { throw 'Demo initialization failed.' } } finally { Pop-Location }
    $node = (Get-Command node -ErrorAction Stop).Source
    $vite = Join-Path $frontend 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite)) {
        Push-Location $frontend
        try { & npm.cmd ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' } } finally { Pop-Location }
    }
    $index = Join-Path $frontend 'dist\index.html'
    $newest = Get-ChildItem -LiteralPath (Join-Path $frontend 'src') -Recurse -File | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not (Test-Path -LiteralPath $index) -or $newest.LastWriteTime -gt (Get-Item -LiteralPath $index).LastWriteTime) {
        Push-Location $frontend
        try { & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' } } finally { Pop-Location }
    }
    $backendPort = Port-Owner 8000
    if ($backendPort) {
        $health = Read-Health
        if (-not $health -or $health.project -ne 'qinghe-sol' -or -not (Test-QingheProcess $backendPort.OwningProcess 'app.main:app' $projectRoot)) { throw 'Port 8000 is used by another application. It was not stopped.' }
    } else {
        Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.main:app','--app-dir',('"' + $backend + '"'),'--host','127.0.0.1','--port','8000') -WorkingDirectory $backend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'backend.log') -RedirectStandardError (Join-Path $runtime 'backend-error.log') | Out-Null
    }
    $frontendPort = Port-Owner 5173
    if ($frontendPort) {
        if (-not (Test-QingheProcess $frontendPort.OwningProcess 'vite' $projectRoot)) { throw 'Port 5173 is used by another application. It was not stopped.' }
    } else {
        Start-Process -FilePath $node -ArgumentList @(('"' + $vite + '"'),'preview','--host','127.0.0.1','--port','5173','--strictPort') -WorkingDirectory $frontend -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'frontend.log') -RedirectStandardError (Join-Path $runtime 'frontend-error.log') | Out-Null
    }
    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $health = Read-Health
        $frontOK = $false
        try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5173' -UseBasicParsing -TimeoutSec 2; $frontOK = $r.StatusCode -eq 200 } catch {}
        if ($health -and $health.project -eq 'qinghe-sol' -and $frontOK) { $ready = $true; break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Services did not become ready. Check work\runtime logs.' }
    $tracked = @()
    foreach ($port in @(8000,5173)) {
        $listener = Port-Owner $port
        $proc = Get-Process -Id $listener.OwningProcess
        $tracked += @{ pid=$proc.Id; port=$port; startTicks=$proc.StartTime.ToUniversalTime().Ticks.ToString() }
    }
    @{ root=$projectRoot; processes=$tracked } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtime 'services.json') -Encoding UTF8
    Write-Host 'Qinghe is ready: http://127.0.0.1:5173' -ForegroundColor Green
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:5173' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
