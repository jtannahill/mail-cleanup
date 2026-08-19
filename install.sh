#!/bin/bash
#
# Install Mail Cleanup: scripts, Python venv, menubar app, launchd agent.
#
# Usage:
#   ./install.sh [--prefix DIR] [--schedule HH:MM] [--no-menubar]
#
#   --prefix DIR      Install location (default: ~/.local/share/mail-cleanup)
#   --schedule HH:MM  Also run "--all --quiet" daily at HH:MM via launchd
#   --no-menubar      Skip the menubar agent (CLI + app only)
#
# Safe to re-run; it refreshes the install in place.
#

set -euo pipefail

# Resolve symlinks (Homebrew links this script into bin) so sibling files are
# found next to the real script.
self="$0"
while [ -L "$self" ]; do
  target="$(readlink "$self")"
  case "$target" in
    /*) self="$target" ;;
    *) self="$(dirname "$self")/$target" ;;
  esac
done
SRC="$(cd "$(dirname "$self")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local/share/mail-cleanup}"
SCHEDULE=""
MENUBAR=1

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --schedule) SCHEDULE="$2"; shift 2 ;;
    --no-menubar) MENUBAR=0; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

AGENTS="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"
MENUBAR_LABEL="com.mail-cleanup.menubar"
SCHED_LABEL="com.mail-cleanup.scheduled"

say() { printf '==> %s\n' "$*"; }

render() {
  # render TEMPLATE DEST ; substitutes @PREFIX@ @HOME@ @HOUR@ @MINUTE@
  sed -e "s|@PREFIX@|$PREFIX|g" -e "s|@HOME@|$HOME|g" \
      -e "s|@HOUR@|${HOUR:-0}|g" -e "s|@MINUTE@|${MINUTE:-0}|g" "$1" > "$2"
}

unload() {
  launchctl bootout "gui/$UID_NUM/$1" >/dev/null 2>&1 || true
}

load() {
  # bootout is asynchronous; a bootstrap issued straight after it can fail
  # with "service already loaded", so retry briefly.
  local _try
  for _try in 1 2 3 4 5; do
    if launchctl bootstrap "gui/$UID_NUM" "$1" 2>/dev/null; then return 0; fi
    sleep 1
  done
  echo "warning: could not load $2; run: launchctl bootstrap gui/$UID_NUM $1" >&2
}

say "Installing to $PREFIX"
mkdir -p "$PREFIX" "$AGENTS" "$HOME/Library/Logs" "$HOME/.config/mail-cleanup"
cp "$SRC/mail-cleanup.applescript" "$SRC/mail-cleanup.sh" "$SRC/mail-cleanup-menubar.py" \
   "$SRC/build-app.sh" "$SRC/requirements.txt" "$PREFIX/"
chmod +x "$PREFIX/mail-cleanup.sh" "$PREFIX/build-app.sh"

if [ ! -f "$HOME/.config/mail-cleanup/rules.txt" ]; then
  cat > "$HOME/.config/mail-cleanup/rules.txt" <<'RULES'
# Mail cleanup rules: one per line, case-insensitive contains match on inbox mail.
#   from:someone@example.com
#   subject:Weekly Digest
RULES
fi

if [ "$MENUBAR" = 1 ]; then
  say "Creating Python environment"
  if command -v uv >/dev/null 2>&1; then
    [ -x "$PREFIX/.venv/bin/python3" ] || uv venv --quiet "$PREFIX/.venv"
    uv pip install --quiet --python "$PREFIX/.venv/bin/python3" -r "$PREFIX/requirements.txt"
  else
    [ -x "$PREFIX/.venv/bin/python3" ] || python3 -m venv "$PREFIX/.venv"
    "$PREFIX/.venv/bin/python3" -m pip install --quiet -r "$PREFIX/requirements.txt"
  fi
fi

say "Building Mail Cleanup.app"
"$PREFIX/build-app.sh"

if [ "$MENUBAR" = 1 ]; then
  say "Installing menubar agent"
  unload "$MENUBAR_LABEL"
  render "$SRC/launchd/$MENUBAR_LABEL.plist.in" "$AGENTS/$MENUBAR_LABEL.plist"
  load "$AGENTS/$MENUBAR_LABEL.plist" "$MENUBAR_LABEL"
fi

if [ -n "$SCHEDULE" ]; then
  HOUR="${SCHEDULE%%:*}"; MINUTE="${SCHEDULE##*:}"
  HOUR=$((10#$HOUR)); MINUTE=$((10#$MINUTE))
  say "Installing daily job at $SCHEDULE"
  unload "$SCHED_LABEL"
  render "$SRC/launchd/$SCHED_LABEL.plist.in" "$AGENTS/$SCHED_LABEL.plist"
  load "$AGENTS/$SCHED_LABEL.plist" "$SCHED_LABEL"
fi

cat <<MSG

Installed. Two one-time macOS permissions are needed:

  1. Automation: the first run asks "Mail Cleanup wants access to control Mail".
     Click Allow. Trigger it now with:
       open -W --env "MAIL_CLEANUP_ARGS=--all --dry-run" -a "Mail Cleanup"

  2. Accessibility (needed only to empty the trash): System Settings >
     Privacy & Security > Accessibility > add
       ~/Applications/Mail Cleanup.app
     You must re-add it after every rebuild of the app.

CLI: $PREFIX/mail-cleanup.sh --help
Rules: ~/.config/mail-cleanup/rules.txt
Log:   ~/Library/Logs/mail-cleanup.log
MSG
