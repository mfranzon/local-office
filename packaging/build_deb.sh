#!/usr/bin/env bash
# Builds dist/local-office_<version>_amd64.deb on Ubuntu 24.04 (Python 3.12). The window uses the system's GTK and
# WebKit through python3-gi, so they are dependencies instead of being bundled; the Python packages go in a private
# venv in /opt/local-office. Install with: sudo apt install ./dist/local-office_*.deb
set -euo pipefail
umask 022
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"; DIST="$ROOT/dist"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/backend/pyproject.toml")"
PKG="$BUILD/deb"; OPT="$PKG/opt/local-office"
cd "$ROOT"

[ -x "$BUILD/bin/llama-server" ] || packaging/build_llama.sh
[ -n "${SKIP_UI:-}" ] || (cd frontend && npm ci --no-audit --no-fund --silent && npm run build)
uv run -q packaging/make_icon.py "$BUILD"

rm -rf "$PKG"; mkdir -p "$OPT/backend" "$OPT/build/bin" "$OPT/frontend" "$PKG/DEBIAN" "$PKG/usr/bin" \
  "$PKG/usr/share/applications" "$PKG/usr/share/icons/hicolor/512x512/apps"
# Same layout as the repo, so config.py finds the UI, skills and llama-server without extra settings.
cp -R backend/app "$OPT/backend/"; cp -R frontend/dist "$OPT/frontend/"; cp -R skills "$OPT/"
cp "$BUILD/bin/llama-server" "$OPT/build/bin/"
find "$OPT" -name __pycache__ -prune -exec rm -rf {} +

# venv on the system Python, which sees python3-gi; locked versions from uv.lock
uv venv -q --python /usr/bin/python3.12 --system-site-packages "$OPT/venv"
(cd backend && uv export -q --frozen --no-dev --no-emit-project --no-hashes -o "$BUILD/requirements.txt")
uv pip install -q --python "$OPT/venv/bin/python" -r "$BUILD/requirements.txt"
"$OPT/venv/bin/python" -m compileall -q "$OPT/backend" "$OPT/venv/lib"

cat > "$PKG/usr/bin/local-office" <<'SH'
#!/bin/sh
cd /opt/local-office/backend && exec /opt/local-office/venv/bin/python -m app.desktop "$@"
SH
chmod 755 "$PKG/usr/bin/local-office"
cp "$BUILD/LocalOffice.iconset/icon_256x256@2x.png" "$PKG/usr/share/icons/hicolor/512x512/apps/local-office.png"
cat > "$PKG/usr/share/applications/local-office.desktop" <<'INI'
[Desktop Entry]
Type=Application
Name=Local Office
Comment=Private AI for your office documents
Exec=local-office
Icon=local-office
Categories=Office;
StartupWMClass=local-office
INI

# CUDA runtime libraries from Ubuntu's archive when llama-server was built with CUDA; the NVIDIA driver is not
# a dependency (installed separately, any version supporting the toolkit)
DEPENDS="python3.12, python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1"
ldd "$OPT/build/bin/llama-server" | grep -q libcudart && DEPENDS+=", libcudart12, libcublas12, libcublaslt12"
cat > "$PKG/DEBIAN/control" <<CTRL
Package: local-office
Version: $VERSION
Architecture: amd64
Maintainer: Marco Franzon <marco.franzon@exact-lab.it>
Depends: $DEPENDS
Section: office
Priority: optional
Installed-Size: $(du -sk "$PKG" | cut -f1)
Description: Private AI for the office
 Ask questions about scans, PDFs and Office files and get answers that cite the page.
 Models run on this computer; no file leaves it.
CTRL

rm -f "$OPT/venv/.lock"; chmod -R u+rwX,go+rX,go-w "$PKG"   # uv keeps its cache's permissions
mkdir -p "$DIST"
dpkg-deb --build --root-owner-group -Zxz "$PKG" "$DIST/local-office_${VERSION}_amd64.deb" >/dev/null
echo "built $DIST/local-office_${VERSION}_amd64.deb ($(du -h "$DIST/local-office_${VERSION}_amd64.deb" | cut -f1))"
