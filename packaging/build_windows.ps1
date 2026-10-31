# Builds dist\LocalOffice-Setup.exe: llama-server (official llama.cpp Windows build) + UI + Python backend,
# frozen with PyInstaller and packed with Inno Setup. Needs uv, Node 22 and Inno Setup 6.
# Unsigned: SmartScreen shows "Windows protected your PC" (More info -> Run anyway) until it's code-signed.
$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path build\bin\llama-server.exe)) { uv run --no-project python packaging\fetch_llama_windows.py }
Push-Location frontend; npm ci --no-audit --no-fund --silent; npm run build; Pop-Location
uv run -q packaging\make_icon.py build
Push-Location backend
uv sync -q
uv run pyinstaller --noconfirm --clean --log-level WARN --distpath "$Root\dist" --workpath "$Root\build\pyinstaller" "$Root\packaging\LocalOffice.spec"
Pop-Location

$Version = (Select-String -Path backend\pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) { $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $Iscc /Q "/DAppVersion=$Version" packaging\LocalOffice.iss
$Size = [math]::Round((Get-Item dist\LocalOffice-Setup.exe).Length / 1MB)
Write-Host "built dist\LocalOffice-Setup.exe ($Size MB)"
