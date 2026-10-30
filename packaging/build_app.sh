#!/usr/bin/env bash
# Builds dist/LocalOffice.dmg: llama-server (static) + UI + Python backend in a native .app.
# Signing: ad-hoc by default. With an Apple Developer ID, set SIGN_IDENTITY="Developer ID Application: ..."
# and NOTARY_PROFILE (xcrun notarytool store-credentials) to get a dmg that opens without warnings.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"; DIST="$ROOT/dist"; APP="$DIST/Local Office.app"
SIGN_IDENTITY="${SIGN_IDENTITY:--}"
cd "$ROOT"

[ -x "$BUILD/bin/llama-server" ] || packaging/build_llama.sh
(cd frontend && npm ci --no-audit --no-fund --silent && npm run build)
uv run -q packaging/make_icon.py "$BUILD"
(cd backend && uv sync -q && uv run pyinstaller --noconfirm --clean --log-level WARN \
  --distpath "$DIST" --workpath "$BUILD/pyinstaller" "$ROOT/packaging/LocalOffice.spec")

if [ "$SIGN_IDENTITY" = "-" ]; then
  codesign --force --deep --sign - "$APP"            # ad-hoc: runs locally; other Macs show a Gatekeeper warning
else
  codesign --force --deep --options runtime --timestamp --entitlements packaging/entitlements.plist \
    --sign "$SIGN_IDENTITY" "$APP"
fi
codesign --verify --deep --strict "$APP"

STAGE="$BUILD/dmg"; rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
rm -f "$DIST/LocalOffice.dmg"
hdiutil create -volname "Local Office" -srcfolder "$STAGE" -fs HFS+ -format UDZO "$DIST/LocalOffice.dmg" -quiet

if [ -n "${NOTARY_PROFILE:-}" ] && [ "$SIGN_IDENTITY" != "-" ]; then
  codesign --sign "$SIGN_IDENTITY" "$DIST/LocalOffice.dmg"
  xcrun notarytool submit "$DIST/LocalOffice.dmg" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DIST/LocalOffice.dmg"
fi
echo "built $DIST/LocalOffice.dmg ($(du -h "$DIST/LocalOffice.dmg" | cut -f1))"
