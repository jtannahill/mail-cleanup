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

# osacompile ad-hoc signs the bundle, and editing Info.plist afterwards breaks
# that seal. tccd then cannot compute a code requirement for the app, so it
# neither prompts for nor honors Automation/Accessibility grants and every
# Apple event to Mail fails with -1743. Re-sign so the seal matches.
# Ad-hoc signatures change with every build, and macOS ties Automation and
# Accessibility grants to the signature, so each rebuild forces the user to
# re-grant. Set CODESIGN_IDENTITY to a Developer ID or self-signed code-signing
# certificate in your keychain to get a stable identity that survives rebuilds.
codesign --force --sign "${CODESIGN_IDENTITY:--}" "$APP"
codesign --verify --verbose=1 "$APP"

echo "Rebuilt: $APP"
