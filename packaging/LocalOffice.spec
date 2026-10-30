# PyInstaller spec for Local Office.app (run via packaging/build_app.sh)
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [(str(ROOT / "frontend/dist"), "ui")] + collect_data_files("docx") + collect_data_files("pypdfium2_raw")
binaries = [(str(ROOT / "build/bin/llama-server"), "bin")]
binaries += collect_dynamic_libs("sqlite_vec") + collect_dynamic_libs("pypdfium2_raw") + collect_dynamic_libs("pillow_heif")
hiddenimports = collect_submodules("uvicorn") + ["app.main"]
for pkg in ("webview", "sqlite_vec"):
    d, b, h = collect_all(pkg)
    datas, binaries, hiddenimports = datas + d, binaries + b, hiddenimports + h

a = Analysis([str(ROOT / "packaging/launch.py")], pathex=[str(ROOT / "backend")], datas=datas, binaries=binaries,
             hiddenimports=hiddenimports, excludes=["tkinter", "pytest", "IPython"])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Local Office", console=False, target_arch="arm64")
coll = COLLECT(exe, a.binaries, a.datas, name="Local Office")
app = BUNDLE(
    coll, name="Local Office.app", icon=str(ROOT / "build/LocalOffice.icns"), bundle_identifier="app.localoffice.desktop",
    info_plist={
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSDocumentsFolderUsageDescription": "Local Office reads documents you choose so you can search and ask about them.",
        "NSDesktopFolderUsageDescription": "Local Office reads documents you choose so you can search and ask about them.",
        "NSDownloadsFolderUsageDescription": "Local Office reads documents you choose so you can search and ask about them.",
    },
)
