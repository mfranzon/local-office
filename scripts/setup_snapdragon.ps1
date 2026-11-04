$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$Root = Split-Path -Parent $PSScriptRoot
$env:LOCAL_OFFICE_BACKEND = 'snapdragon'
$env:MODELS_DIR = Join-Path $Root 'models'
$env:GENIEX_SKIP_SDK_DOWNLOAD = '1'
$env:PYTHONUTF8 = '1'
Push-Location (Join-Path $Root 'backend')
try {
    uv sync --extra snapdragon --python '3.12'
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency setup failed.' }
    & .venv\Scripts\python.exe -m app.provision
    if ($LASTEXITCODE -ne 0) { throw 'Model or runtime setup failed.' }
} finally { Pop-Location }
Push-Location (Join-Path $Root 'frontend')
try {
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency setup failed.' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Push-Location (Join-Path $Root 'desktop')
try {
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency setup failed.' }
    node node_modules/electron/install.js
    if ($LASTEXITCODE -ne 0) { throw 'Desktop runtime setup failed.' }
} finally { Pop-Location }
Write-Host 'Ready. Open Start Local Office.cmd.'
