#!/usr/bin/env python3
"""Menu bar controls for Apple Mail cleanup.

AppKit only tolerates UI mutation from the main thread, so every write to a
menu title goes through AppHelper.callAfter. Work that shells out to
osascript runs on a worker thread and marshals its result back.
"""

import os
import re
import subprocess
from collections import Counter
import threading
from pathlib import Path

import rumps
from AppKit import NSAlert, NSApplication, NSImage, NSWorkspace
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
DETAIL_PATTERN = re.compile(r"^(.+?)/(.+?): (\d+)$")
ADDRESS_PATTERN = re.compile(r"<([^>]+)>")
TOP_SENDERS = 12


def _run(args, timeout, env=None):
    """Run the cleanup script. Returns its output text, or None on failure.

    osascript sends `log` lines (the dry-run report) to stderr and the
    script's return value (the --senders dump) to stdout; both are wanted."""
    try:
        proc = subprocess.run(
            [str(CLEANUP), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout + proc.stderr


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


def parse_counts(text):
    counts = {"Rules": 0, "Junk": 0, "Trash": 0}
    for line in text.splitlines():
        m = COUNT_PATTERN.match(line.strip())
        if m:
            counts[m.group(1)] = int(m.group(2))
    return counts["Rules"], counts["Junk"], counts["Trash"]


def parse_details(text):
    """Per-account breakdown from dry-run output.

    Returns a list of (section, account, mailbox, count) in output order, where
    section is Rules, Junk or Trash."""
    out = []
    section = None
    for raw in text.splitlines():
        line = raw.strip()
        m = COUNT_PATTERN.match(line)
        if m:
            section = m.group(1)
            continue
        m = DETAIL_PATTERN.match(line)
        if m and section and raw.startswith("  "):
            out.append((section, m.group(1), m.group(2), int(m.group(3))))
    return out


def fetch_counts():
    """Returns ((rules, junk, trash), details), or ((None,)*3, []) if Mail
    could not be read."""
    stderr = _run(["--all", "--dry-run"], COUNT_TIMEOUT)
    if stderr is None:
        return (None, None, None), []
    return parse_counts(stderr), parse_details(stderr)


def sender_address(sender):
    """'Name <a@b.c>' -> 'a@b.c'; bare addresses pass through."""
    m = ADDRESS_PATTERN.search(sender)
    return (m.group(1) if m else sender).strip().lower()


def top_senders(text, limit=TOP_SENDERS):
    """Tally a --senders dump by address. Returns [(address, count)]."""
    tally = Counter(sender_address(line) for line in text.splitlines() if line.strip())
    return tally.most_common(limit)


def fetch_top_senders():
    out = _run(["--senders"], COUNT_TIMEOUT)
    if out is None:
        return []
    return top_senders(out)


def preview_rule(rule):
    """How many inbox messages a single rule would trash right now, or None."""
    stderr = _run(
        ["--rules", "--dry-run"], COUNT_TIMEOUT,
        env={"MAIL_CLEANUP_RULES_OVERRIDE": rule},
    )
    if stderr is None:
        return None
    return parse_counts(stderr)[0]


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
            or line.startswith("keep:")
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


_MAIL_ICON = None


def mail_icon():
    """Mail's own app icon, for dialogs. Falls back to the generic app icon."""
    global _MAIL_ICON
    if _MAIL_ICON is None:
        ws = NSWorkspace.sharedWorkspace()
        path = ws.absolutePathForAppBundleWithIdentifier_("com.apple.mail")
        _MAIL_ICON = ws.iconForFile_(path) if path else NSImage.imageNamed_("NSApplicationIcon")
    return _MAIL_ICON


def confirm(title, body, ok="OK", cancel="Cancel"):
    """Modal yes/no with Mail's icon. Returns True when the ok button is hit."""
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(body)
    alert.setIcon_(mail_icon())
    alert.addButtonWithTitle_(ok)
    alert.addButtonWithTitle_(cancel)
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    return alert.runModal() == 1000  # NSAlertFirstButtonReturn


def item(title, callback=None, tip=None):
    """rumps.MenuItem with an optional hover tooltip (NSMenuItem toolTip)."""
    mi = rumps.MenuItem(title, callback=callback)
    if tip:
        mi._menuitem.setToolTip_(tip)
    return mi


class MailCleanupMenuBar(rumps.App):
    def __init__(self):
        super().__init__("✉", quit_button=None)
        self.busy = False
        # Last known (rules, junk, trash). Used as the "before" figure for an
        # erase so each click costs one count pass, not two.
        self.counts = (None, None, None)

        self.status_item = rumps.MenuItem("Checking…")
        self.accounts_item = item(
            "By Account", None, "Junk, Deleted and Matched counts per account.")
        self.accounts_item.add(rumps.MenuItem("Checking…"))
        self.senders_item = item(
            "Top Senders", None,
            "Most frequent senders in your inboxes. Click one to add a from: rule for it.")
        self.senders_item.add(rumps.MenuItem("Checking…"))
        self.menu = [
            self.status_item,
            self.accounts_item,
            None,
            self.senders_item,
            None,
            item("Clear Matched", self.clear_matched,
                 "Move inbox messages that match your rules to the Trash."),
            item("Clear Junk", self.clear_junk,
                 "Move everything in Junk/Spam in every account to the Trash."),
            item("Clear Deleted", self.clear_deleted,
                 "Permanently empty the Trash in every account. Needs Accessibility."),
            item("Clear All", self.clear_all,
                 "Rules, then Junk, then Deleted, in one pass."),
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

    def _apply_counts(self, rules, junk, trash, details=None):
        self.counts = (rules, junk, trash)
        if junk is None:
            self.title = "✉"
            self.status_item.title = "Mail counts unavailable"
            return
        total = rules + junk + trash
        self.title = "✉" if total == 0 else f"✉ {total}"
        self.status_item.title = (
            f"Junk: {junk} · Deleted: {trash} · Matched: {rules}"
        )
        if details is not None:
            self._fill_accounts_menu(details)

    def _fill_accounts_menu(self, details):
        per = {}
        for section, account, _mailbox, n in details:
            per.setdefault(account, {"Junk": 0, "Trash": 0, "Rules": 0})
            per[account][section] += n
        menu = self.accounts_item
        if len(menu):
            menu.clear()
        if not per:
            menu.add(rumps.MenuItem("Nothing to clean"))
            return
        for account in sorted(per):
            c = per[account]
            menu.add(rumps.MenuItem(
                f"{account}: junk {c['Junk']} · deleted {c['Trash']} · matched {c['Rules']}"
            ))

    def _apply_top_senders(self, senders):
        menu = self.senders_item
        if len(menu):
            menu.clear()
        if not senders:
            menu.add(rumps.MenuItem("No inbox mail found"))
            return
        rules = set(load_rules())
        for address, n in senders:
            rule = f"from:{address}"
            if rule in rules:
                menu.add(item(f"{n:>5}  {address}  (rule exists)", None,
                              "A from: rule for this address is already active."))
            elif f"keep:{address}" in rules:
                menu.add(item(f"{n:>5}  {address}  (kept)", None,
                              "This address is on your keep list."))
            else:
                menu.add(item(f"{n:>5}  {address}", self._top_sender_callback(address),
                              f"Add a rule to trash inbox mail from {address}."))

    def _top_sender_callback(self, address):
        def cb(_):
            self._preview_and_add("from", address)
        return cb

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
        menu.add(item(
            "Add Sender Rule…", self.add_sender_rule,
            "Trash inbox mail whose sender (name or address) contains the text.\n"
            "Case-insensitive, partial match. Add ' older:30d' to only trash mail older than 30 days.\n"
            "Examples: news@example.com, @marketing.example.net, Acme Sales older:14d",
        ))
        menu.add(item(
            "Add Subject Rule…", self.add_subject_rule,
            "Trash inbox mail whose subject contains the text.\n"
            "Case-insensitive, partial match. Add ' older:30d' to only trash mail older than 30 days.\n"
            "Examples: Weekly Digest, your invoice, [Newsletter] older:7d",
        ))
        menu.add(item(
            "Add Keep Rule…", self.add_keep_rule,
            "Never trash mail whose sender contains the text, even if another rule matches.\n"
            "Examples: boss@work.com, @mycompany.com",
        ))
        menu.add(item(
            "Edit Rules File", self.open_rules,
            f"Open {RULES} in your editor.\n"
            "One rule per line: from:<text>, subject:<text> or keep:<text>, optionally followed by\n"
            "' older:30d'. Lines starting with # are comments.",
        ))
        rules = load_rules()
        if rules:
            menu.add(None)
            for rule in rules:
                menu.add(item(
                    rule, self._remove_rule_callback(rule),
                    "Safelist: never trashed by a rule. Click to remove."
                    if rule.startswith("keep:") else "Click to remove this rule.",
                ))

    def _refresh_rules_menu(self):
        self._fill_rules_menu(self.menu["Rules"])

    def _remove_rule_callback(self, rule):
        def cb(_):
            if confirm(rule, "Remove this rule?", ok="Remove"):
                remove_rule(rule)
                self._refresh_rules_menu()
                self.refresh_now(None)
        return cb

    @staticmethod
    def _bring_to_front():
        # An accessory (menubar-only) app is never the active app, so a
        # dialog it opens does not receive keystrokes unless we activate it
        # right before showing the window.
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _prompt_rule(self, kind, title, message, placeholder):
        win = rumps.Window(
            message=message, title=title, ok="Add", cancel="Cancel", dimensions=(380, 24)
        )
        win._alert.setIcon_(mail_icon())
        win._textfield.setPlaceholderString_(placeholder)
        self._bring_to_front()
        win._alert.window().makeFirstResponder_(win._textfield)
        resp = win.run()
        if resp.clicked != 1:
            return
        text = resp.text.strip()
        if not text:
            return
        self._preview_and_add(kind, text)

    def _preview_and_add(self, kind, text):
        rule = f"{kind}:{text}"
        if kind == "keep":
            # Nothing to preview: a keep rule only ever reduces what is trashed.
            self._confirm_rule(rule, None)
            return
        self._set_status("Previewing rule…")

        def work():
            n = preview_rule(rule)
            AppHelper.callAfter(self._confirm_rule, rule, n)

        threading.Thread(target=work, daemon=True).start()

    def _confirm_rule(self, rule, n):
        if rule.startswith("keep:"):
            body = "Mail from this sender will never be trashed by a rule."
        elif n is None:
            body = "Could not count matches. Is Mail running?"
        elif n == 0:
            body = "Matches no inbox mail right now. It will apply to future mail."
        else:
            body = f"Matches {n} inbox message{'s' if n != 1 else ''} right now."
        if confirm(rule, body, ok="Add Rule"):
            add_rule(*rule.split(":", 1))
            self._refresh_rules_menu()
        self.refresh_now(None)

    def add_sender_rule(self, _):
        self._prompt_rule(
            "from", "Trash inbox mail whose sender contains:",
            "Name or address, partial match, case-insensitive.\n"
            "Append  older:30d  to limit it to older mail.",
            "news@example.com",
        )

    def add_keep_rule(self, _):
        self._prompt_rule(
            "keep", "Never trash mail whose sender contains:",
            "Overrides every other rule. Partial match, case-insensitive.",
            "boss@work.com",
        )

    def add_subject_rule(self, _):
        self._prompt_rule(
            "subject", "Trash inbox mail whose subject contains:",
            "Partial match, case-insensitive.\n"
            "Append  older:30d  to limit it to older mail.",
            "Weekly Digest",
        )

    def open_rules(self, _):
        RULES.parent.mkdir(parents=True, exist_ok=True)
        if not RULES.exists():
            RULES.write_text(RULES_HEADER)
        subprocess.Popen(["open", "-t", str(RULES)])

    # --- background work --------------------------------------------------

    def _refresh_async(self):
        def work():
            (rules, junk, trash), details = fetch_counts()
            AppHelper.callAfter(self._apply_counts, rules, junk, trash, details)
            senders = fetch_top_senders()
            AppHelper.callAfter(self._apply_top_senders, senders)

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

        cached = self.counts

        def work():
            # Counts are refreshed every few minutes and after every action,
            # so the cached figure is a good enough "before". Only fall back
            # to a count pass when we have never managed one.
            before_counts = cached if cached[1] is not None else fetch_counts()[0]
            before = self._measure(mode, *before_counts)

            # The erase is synchronous, so a single re-count afterwards is
            # enough. No polling loop.
            failed = not _run_erase(mode, ERASE_TIMEOUT)
            (rules, junk, trash), details = fetch_counts()

            AppHelper.callAfter(
                self._finish_erase, mode, label, before, rules, junk, trash,
                details, failed,
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

    def _finish_erase(self, mode, label, before, rules, junk, trash, details, failed):
        self.busy = False
        self._apply_counts(rules, junk, trash, details)

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
