#!/bin/bash
#
# Rebuild "Mail Cleanup.app" from mail-cleanup.applescript.
#
# The app exists so the scheduled launchd job has a stable application
# identity to hold the Automation (Apple Events) grant for Mail. It embeds a
# compiled copy of the script, so run this after every edit to the source or
# the app will silently keep running the old logic.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$SCRIPT_DIR/mail-cleanup.applescript"
APP="$HOME/Applications/Mail Cleanup.app"
ICON="$APP/Contents/Resources/applet.icns"
TMP_ICON="$(mktemp -d)/applet.icns"

if [ -f "$ICON" ]; then
  cp "$ICON" "$TMP_ICON"
fi

rm -rf "$APP"
osacompile -o "$APP" "$SOURCE"

if [ -f "$TMP_ICON" ]; then
  cp "$TMP_ICON" "$APP/Contents/Resources/applet.icns"
fi

# Keep the applet out of the Dock.
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" \
  "$APP/Contents/Info.plist" 2>/dev/null || true

# osacompile leaves the bundle without an identifier, so macOS keys its TCC
# grants on the file path alone. A stable identifier keeps them addressable
# (tccutil reset Accessibility com.mail-cleanup.app) and more resilient.
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.mail-cleanup.app" \
  "$APP/Contents/Info.plist" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.mail-cleanup.app" \
  "$APP/Contents/Info.plist"

# osacompile ad-hoc signs the bundle, and editing Info.plist afterwards breaks
# that seal. tccd then cannot compute a code requirement for the app, so it
# neither prompts for nor honors Automation/Accessibility grants and every
# Apple event to Mail fails with -1743. Re-sign so the seal matches.
# Ad-hoc signatures change with every build, and macOS ties Automation and
# Accessibility grants to the signature, so each rebuild forces the user to
# re-grant. Set CODESIGN_IDENTITY to a Developer ID or self-signed code-signing
# certificate in your keychain to get a stable identity that survives rebuilds.
# Pick the identity: CODESIGN_IDENTITY if set, else the first valid
# code-signing certificate in the keychain (an Apple Development or Developer
# ID cert is stable across rebuilds), else ad-hoc as a last resort.
IDENTITY="${CODESIGN_IDENTITY:-}"
if [ -z "$IDENTITY" ]; then
  IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/^ *1) \([0-9A-F]\{40\}\) .*/\1/p')"
fi
if [ -z "$IDENTITY" ]; then
  IDENTITY="-"
  echo "WARNING: no code-signing certificate found; signing ad-hoc." >&2
  echo "         Accessibility must be re-granted after every rebuild." >&2
else
  echo "Signing with: $(security find-identity -v -p codesigning | grep "$IDENTITY" | sed 's/^ *[0-9]*) [0-9A-F]* //')"
fi
codesign --force --sign "$IDENTITY" "$APP"
codesign --verify --verbose=1 "$APP"

echo "Rebuilt: $APP"
