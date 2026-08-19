#!/usr/bin/env python3
"""Menu bar controls for Apple Mail cleanup.

AppKit only tolerates UI mutation from the main thread, so every write to a
menu title goes through AppHelper.callAfter. Work that shells out to
osascript runs on a worker thread and marshals its result back.
"""

import re
import subprocess
import threading
from pathlib import Path

import rumps
from PyObjCTools import AppHelper

BIN = Path(__file__).resolve().parent
CLEANUP = BIN / "mail-cleanup.sh"
LOG = Path.home() / "Library" / "Logs" / "mail-cleanup.log"
RULES = Path.home() / ".config" / "mail-cleanup" / "rules.txt"
RULES_HEADER = (
    "# Mail cleanup rules: one per line, case-insensitive contains match on inbox mail.\n"
    "#   from:someone@example.com\n"
    "#   subject:Weekly Digest\n"
)
MAIL_CLEANUP_APP = "Mail Cleanup"

REFRESH_SECONDS = 300
COUNT_TIMEOUT = 120
ERASE_TIMEOUT = 600

COUNT_PATTERN = re.compile(r"^(Rules|Junk|Trash): would erase (\d+) messages")


def _run(args, timeout):
    """Run the cleanup script. Returns stderr text, or None on failure."""
    try:
        proc = subprocess.run(
            [str(CLEANUP), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stderr


def _run_erase(mode, timeout):
    """Erase via Mail Cleanup.app.

    Clearing trash drives Mail's menu bar, which needs Accessibility. That
    grant is held by the app bundle, not by this process, so the erase has to
    go through the app. -W blocks until it exits, so no polling is needed.
    """
    try:
        proc = subprocess.run(
            [
                "open", "-g", "-W",
                "--env", f"MAIL_CLEANUP_ARGS={mode} --quiet",
                "-a", MAIL_CLEANUP_APP,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def fetch_counts():
    """Returns (rules, junk, trash), or (None, None, None) if Mail could not be read."""
    stderr = _run(["--all", "--dry-run"], COUNT_TIMEOUT)
    if stderr is None:
        return None, None, None

    counts = {"Rules": 0, "Junk": 0, "Trash": 0}
    for line in stderr.splitlines():
        m = COUNT_PATTERN.match(line.strip())
        if m:
            counts[m.group(1)] = int(m.group(2))
    return counts["Rules"], counts["Junk"], counts["Trash"]


# --- rules file -----------------------------------------------------------

def load_rules():
    """Returns the active rule lines (kind:text), comments and blanks dropped."""
    if not RULES.exists():
        return []
    out = []
    for line in RULES.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and (
            line.startswith("from:") or line.startswith("subject:")
        ):
            out.append(line)
    return out


def add_rule(kind, text):
    RULES.parent.mkdir(parents=True, exist_ok=True)
    if not RULES.exists():
        RULES.write_text(RULES_HEADER)
    body = RULES.read_text()
    if body and not body.endswith("\n"):
        body += "\n"
    with RULES.open("w") as fh:
        fh.write(body + f"{kind}:{text}\n")


def remove_rule(rule):
    if not RULES.exists():
        return
    lines = RULES.read_text().splitlines()
    kept = [ln for ln in lines if ln.strip() != rule]
    RULES.write_text("\n".join(kept) + ("\n" if kept else ""))


class MailCleanupMenuBar(rumps.App):
    def __init__(self):
        super().__init__("✉", quit_button=None)
        self.busy = False

        self.status_item = rumps.MenuItem("Checking…")
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("Clear Matched", callback=self.clear_matched),
            rumps.MenuItem("Clear Junk", callback=self.clear_junk),
            rumps.MenuItem("Clear Deleted", callback=self.clear_deleted),
            rumps.MenuItem("Clear All", callback=self.clear_all),
            None,
            self._build_rules_menu(),
            None,
            rumps.MenuItem("Refresh Counts", callback=self.refresh_now),
            rumps.MenuItem("Open Log", callback=self.open_log),
            None,
            rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application()),
        ]

        # The 300s timer does not fire until its first interval elapses, so
        # seed the counts now rather than showing "Checking…" for five minutes.
        self._refresh_async()

    # --- main-thread UI writes -------------------------------------------

    def _set_status(self, text):
        self.status_item.title = text

    def _apply_counts(self, rules, junk, trash):
        if junk is None:
            self.title = "✉"
            self.status_item.title = "Mail counts unavailable"
            return
        total = rules + junk + trash
        self.title = "✉" if total == 0 else f"✉ {total}"
        self.status_item.title = (
            f"Junk: {junk} · Deleted: {trash} · Matched: {rules}"
        )

    # --- rules menu -------------------------------------------------------

    def _build_rules_menu(self):
        menu = rumps.MenuItem("Rules")
        self._fill_rules_menu(menu)
        return menu

    def _fill_rules_menu(self, menu):
        # rumps only creates the underlying NSMenu once an item is added, so
        # clear() on a fresh submenu raises. Guard against that first fill.
        if len(menu):
            menu.clear()
        menu.add(rumps.MenuItem("Add Sender Rule…", callback=self.add_sender_rule))
        menu.add(rumps.MenuItem("Add Subject Rule…", callback=self.add_subject_rule))
        menu.add(rumps.MenuItem("Edit Rules File", callback=self.open_rules))
        rules = load_rules()
        if rules:
            menu.add(None)
            for rule in rules:
                item = rumps.MenuItem(rule, callback=self._remove_rule_callback(rule))
                menu.add(item)

    def _refresh_rules_menu(self):
        self._fill_rules_menu(self.menu["Rules"])

    def _remove_rule_callback(self, rule):
        def cb(_):
            answer = rumps.alert(
                "Remove rule?", rule, ok="Remove", cancel="Cancel"
            )
            if answer == 1:
                remove_rule(rule)
                self._refresh_rules_menu()
                self.refresh_now(None)
        return cb

    def _prompt_rule(self, kind, title, message):
        win = rumps.Window(
            message=message, title=title, ok="Add", cancel="Cancel", dimensions=(320, 24)
        )
        resp = win.run()
        if resp.clicked != 1:
            return
        text = resp.text.strip()
        if not text:
            return
        add_rule(kind, text)
        self._refresh_rules_menu()
        self.refresh_now(None)

    def add_sender_rule(self, _):
        self._prompt_rule(
            "from", "Add Sender Rule",
            "Trash inbox mail whose sender contains (case-insensitive):",
        )

    def add_subject_rule(self, _):
        self._prompt_rule(
            "subject", "Add Subject Rule",
            "Trash inbox mail whose subject contains (case-insensitive):",
        )

    def open_rules(self, _):
        RULES.parent.mkdir(parents=True, exist_ok=True)
        if not RULES.exists():
            RULES.write_text(RULES_HEADER)
        subprocess.Popen(["open", "-t", str(RULES)])

    # --- background work --------------------------------------------------

    def _refresh_async(self):
        def work():
            rules, junk, trash = fetch_counts()
            AppHelper.callAfter(self._apply_counts, rules, junk, trash)

        threading.Thread(target=work, daemon=True).start()

    @rumps.timer(REFRESH_SECONDS)
    def refresh_counts(self, _):
        if not self.busy:
            self._refresh_async()

    def refresh_now(self, _):
        if self.busy:
            return
        self._set_status("Checking…")
        self._refresh_async()

    def _erase(self, mode, label):
        if self.busy:
            return
        self.busy = True
        self._set_status(f"Erasing {label.lower()}…")

        def work():
            before = self._measure(mode, *fetch_counts())

            # The erase is synchronous, so a single re-count afterwards is
            # enough. No polling loop.
            failed = not _run_erase(mode, ERASE_TIMEOUT)
            rules, junk, trash = fetch_counts()

            AppHelper.callAfter(
                self._finish_erase, mode, label, before, rules, junk, trash, failed
            )

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _measure(mode, rules, junk, trash):
        """Count for the mailboxes a mode acts on. Compare like with like:
        clearing junk or matched mail moves messages into the trash, so
        measuring --junk/--rules against the combined total would under-report."""
        rules, junk, trash = rules or 0, junk or 0, trash or 0
        if mode == "--rules":
            return rules
        if mode == "--junk":
            return junk
        if mode == "--trash":
            return trash
        return rules + junk + trash

    def _finish_erase(self, mode, label, before, rules, junk, trash, failed):
        self.busy = False
        self._apply_counts(rules, junk, trash)

        if failed or junk is None:
            rumps.notification(
                "Mail Cleanup", f"{label} failed", "See the log for details."
            )
            return

        after = self._measure(mode, rules, junk, trash)
        removed = max(before - after, 0)
        if before > 0 and removed == 0:
            rumps.notification(
                "Mail Cleanup",
                f"{label}: nothing removed",
                "See the log. Emptying trash needs Accessibility for Mail Cleanup.app.",
            )
            return
        rumps.notification(
            "Mail Cleanup",
            f"{label} done",
            f"Removed {removed} message{'s' if removed != 1 else ''}.",
        )

    def clear_matched(self, _):
        self._erase("--rules", "Matched mail")

    def clear_junk(self, _):
        self._erase("--junk", "Junk")

    def clear_deleted(self, _):
        self._erase("--trash", "Deleted mail")

    def clear_all(self, _):
        self._erase("--all", "Matched, junk and deleted mail")

    def open_log(self, _):
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.touch(exist_ok=True)
        subprocess.Popen(["open", str(LOG)])


if __name__ == "__main__":
    MailCleanupMenuBar().run()
