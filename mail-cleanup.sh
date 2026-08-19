#!/bin/bash
#
# Apple Mail cleanup across all accounts.
#
# Usage:
#   mail-cleanup.sh [--junk|--trash|--all] [--dry-run] [--quiet]
#
# Trash and junk are cleared via Mail's Erase menus. The parent app running this
# script needs Accessibility permission (Terminal, Cursor, or Mail Cleanup.app).
#
# Defaults to --all. Optionally scheduled via launchd (see install.sh --schedule).
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MAIL_CLEANUP_CLI=1 MAIL_CLEANUP_ARGS="$*" exec osascript "$SCRIPT_DIR/mail-cleanup.applescript"
