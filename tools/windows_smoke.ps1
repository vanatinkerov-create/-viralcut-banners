# Smoke-тест ViralCut на Windows: headless HTTP + мониторинг + журнал.
#
# Запуск (из папки приложения):
#   powershell -ExecutionPolicy Bypass -File tools\windows_smoke.ps1
#   powershell -ExecutionPolicy Bypass -File tools\windows_smoke.ps1 -Exe "dist\ViralCut\ViralCut.exe"
#   powershell -ExecutionPolicy Bypass -File tools\windows_smoke.ps1 -Source
#
# -Source запускает из исходников (нужен .venv-win: install_windows.bat).
param(
    [string]$Exe = "",
    [switch]$Source,
    [int]$Port = 8798
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

$appArgs = @()
if ($Source) {
    $py = Join-Path $PWD ".venv-win\Scripts\python.exe"
    if (-not (Test-Path $py)) { throw "Нет .venv-win — сначала запустите install_windows.bat" }
    $file = $py
    $appArgs = @("desktop\main.py")
} else {
    if (-not $Exe) { $Exe = "dist\ViralCut\ViralCut.exe" }
    if (-not (Test-Path $Exe)) {
        throw "Не найден $Exe. Соберите приложение (build_windows.ps1) или запустите со -Source."
    }
    $file = (Resolve-Path $Exe).Path
}

$env:VIRALCUT_HEADLESS = "1"
$env:VIRALCUT_DATA = Join-Path $env:TEMP "viralcut-smoke-$(Get-Random)"
$env:VIRALCUT_PORT = "$Port"
$base = "http://127.0.0.1:$Port"

Write-Host "[smoke] приложение: $file"
Write-Host "[smoke] данные: $env:VIRALCUT_DATA"

$proc = Start-Process -FilePath $file -ArgumentList $appArgs -PassThru
$failed = $false
try {
    $ok = $false
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $r = Invoke-WebRequest -UseBasicParsing "$base/api/health" -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ok = $true; break }
        } catch { }
    }
    if (-not $ok) { throw "Нет ответа на /api/health за 60 секунд" }

    $health = Invoke-RestMethod "$base/api/health" -TimeoutSec 5
    Write-Host ("[smoke] health: version={0} ffmpeg={1} ffprobe={2} model={3} hw={4}" -f `
        $health.version, $health.ffmpeg, $health.ffprobe, $health.model, $health.hw)
    Write-Host ("[smoke] ffmpeg_path: {0}" -f $health.ffmpeg_path)

    # Мониторинг GPU читает PDH-счётчики — раньше именно здесь падал процесс.
    for ($i = 0; $i -lt 3; $i++) {
        $sys = Invoke-RestMethod "$base/api/system" -TimeoutSec 5
        if ($null -eq $sys.gpu) { throw "/api/system без поля gpu" }
        Write-Host ("[smoke] GPU[{0}]: {1} | {2}% | источник: {3}" -f `
            $i, $sys.gpu.name, $sys.gpu.percent, $sys.gpu.source)
        Start-Sleep -Milliseconds 400
    }

    $diag = Invoke-RestMethod "$base/api/diagnostics?tail=5" -TimeoutSec 5
    Write-Host "[smoke] diagnostics:"
    Write-Host $diag.report

    Write-Host "[smoke] OK"
}
catch {
    $failed = $true
    Write-Host "[smoke] FAIL: $_" -ForegroundColor Red
}
finally {
    if (Test-Path "$env:VIRALCUT_DATA\app.log") {
        Write-Host "--- app.log (хвост) ---"
        Get-Content "$env:VIRALCUT_DATA\app.log" -Tail 120
    }
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}

if ($failed) { exit 1 }
exit 0
