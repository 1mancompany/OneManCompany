#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

APP_NAME="AngryBirdsLite"
BUILD_DIR="$ROOT_DIR/build"
APP_DIR="$BUILD_DIR/${APP_NAME}.app"

echo "[1/3] swift build (release)…"
swift build -c release

BIN_PATH=".build/release/${APP_NAME}"
if [[ ! -f "$BIN_PATH" ]]; then
  echo "Binary not found at $BIN_PATH" >&2
  exit 1
fi

echo "[2/3] packaging .app…"
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cp "$BIN_PATH" "$APP_DIR/Contents/MacOS/${APP_NAME}"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>AngryBirdsLite</string>
  <key>CFBundleIdentifier</key>
  <string>com.onemancompany.angrybirdslite</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>AngryBirdsLite</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

# Attempt ad-hoc codesign if available (not strictly required for local run)
if command -v codesign >/dev/null 2>&1; then
  echo "[3/3] codesign (ad-hoc)…"
  codesign --force --deep --sign - "$APP_DIR" || true
else
  echo "[3/3] skip codesign (codesign not found)"
fi

echo "Done: $APP_DIR"
