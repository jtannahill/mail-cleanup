#!/bin/bash
#
# Remove Mail Cleanup: launchd agents, app bundle, install prefix.
# Rules (~/.config/mail-cleanup) and logs are left in place.
#
set -euo pipefail
PREFIX="${PREFIX:-$HOME/.local/share/mail-cleanup}"
UID_NUM="$(id -u)"
for label in com.mail-cleanup.menubar com.mail-cleanup.scheduled; do
  launchctl bootout "gui/$UID_NUM/$label" >/dev/null 2>&1 || true
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
rm -rf "$HOME/Applications/Mail Cleanup.app" "$PREFIX"
echo "Removed. Kept ~/.config/mail-cleanup and ~/Library/Logs/mail-cleanup*.log"
