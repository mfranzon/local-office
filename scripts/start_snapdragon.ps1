param([switch]$ServerOnly, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Run scripts\setup_snapdragon.ps1 first.'
}
$env:LOCAL_OFFICE_BACKEND = 'snapdragon'
$env:DATA_DIR = Join-Path $Root 'data\snapdragon'
$env:MODELS_DIR = Join-Path $Root 'models'
$env:GENIEX_LIB_PATH = Join-Path $Root 'build\geniex\lib'
$env:GENIEX_PLUGIN_PATH = $env:GENIEX_LIB_PATH
$env:GGML_HEXAGON_DEVICES = 'HTP0'
$env:PYTHONUTF8 = '1'
Set-Location (Join-Path $Root 'backend')
if ($ServerOnly) {
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
} else {
    $Electron = Join-Path $Root 'desktop\node_modules\electron\dist\electron.exe'
    if (-not (Test-Path -LiteralPath $Electron)) { throw 'Run scripts\setup_snapdragon.ps1 first.' }
    & $Electron (Join-Path $Root 'desktop\main.cjs')
}
