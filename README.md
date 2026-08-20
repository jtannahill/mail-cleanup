# Mail Cleanup

Empty Apple Mail's junk and trash across every account, and automatically trash
inbox mail from senders or subjects you never want to see. Runs from a small
menubar app, from the command line, or on a daily schedule. macOS only.

## What it does

- **Junk**: erases every Junk/Spam mailbox in all accounts (moves to Trash).
- **Trash**: permanently empties Trash/Deleted Messages in all accounts.
- **Rules**: trashes inbox messages whose sender or subject contains text you
  list in `~/.config/mail-cleanup/rules.txt`.
- `--all` runs rules, then junk, then trash, so matched and junk mail is gone
  for good in one pass.

Everything is logged to `~/Library/Logs/mail-cleanup.log`.

## Requirements

- macOS 13 or later (tested on macOS 26), Apple Mail with your accounts set up.
- Python 3.9+ for the menubar (only `rumps` is installed, into a private venv).
  [uv](https://docs.astral.sh/uv/) is used if present, otherwise `python3 -m venv`.

## Install

With Homebrew:

```bash
brew tap jtannahill/tap && brew trust jtannahill/tap   # trust is a one-time Homebrew prompt for third-party taps
brew install mail-cleanup
mail-cleanup-setup                  # builds the app, starts the menubar
mail-cleanup-setup --schedule 06:00 # also run "--all" every day at 6 am
```

Or from source:

```bash
git clone https://github.com/jtannahill/mail-cleanup.git
cd mail-cleanup
./install.sh                 # scripts + venv + app + menubar agent
./install.sh --schedule 06:00   # also run "--all" every day at 6 am
```

The installer copies everything to `~/.local/share/mail-cleanup` (override with
`--prefix`), builds `~/Applications/Mail Cleanup.app`, and loads a launchd agent
for the menubar. Re-run it any time to update.

Then grant two permissions (macOS requires both to be done by hand):

1. **Automation**: the first run prompts "Mail Cleanup wants access to control
   Mail". Click Allow. Trigger it with
   `open -W --env "MAIL_CLEANUP_ARGS=--all --dry-run" -a "Mail Cleanup"`.
2. **Accessibility** (only needed to empty the trash): System Settings >
   Privacy & Security > Accessibility > add `~/Applications/Mail Cleanup.app`.

Both grants are tied to the app's code signature. `build-app.sh` signs with the
first valid code-signing certificate in your keychain (Apple Development,
Developer ID, or a self-signed code-signing certificate from Keychain Access),
which keeps the grants stable across rebuilds. Set `CODESIGN_IDENTITY` to pick
a specific one. With no certificate it falls back to ad-hoc signing, which
changes on every rebuild, so you must re-add the app under Accessibility after
each `build-app.sh` or re-install. If the grant is missing, the trash pass logs
the problem and opens the Accessibility pane for you.

## Menubar

An envelope icon appears in the menu bar with the total count of junk, deleted
and rule-matched messages. The menu offers:

- Clear Matched / Clear Junk / Clear Deleted / Clear All
- By Account: junk, deleted and matched counts per account.
- Top Senders: the most frequent senders across your inboxes. Click one to add
  a from: rule for it (after a preview of how many messages it matches).
- Rules: Add Sender Rule, Add Subject Rule, Add Keep Rule, Edit Rules File, and
  one entry per existing rule (click to remove). Adding a rule first shows how
  many inbox messages it matches right now, so you can back out of an
  over-broad one. Hover any item for help.
- Refresh Counts, Open Log, Quit

Counts refresh every 5 minutes and after every action.

## Rules

`~/.config/mail-cleanup/rules.txt`, one rule per line:

```
# comments start with #
from:newsletter@example.com
from:@marketing.example.net
subject:Weekly Digest
from:receipts@shop.example older:30d    # only mail older than 30 days
keep:boss@work.com                      # never trashed, overrides any rule
```

- `from:` and `subject:` rules trash matching inbox mail.
- An optional trailing ` older:Nd` limits a rule to mail received more than N
  days ago, so you can keep recent mail from a sender and purge the backlog.
- `keep:` is a safelist on the sender. Anything matching a keep rule is never
  trashed by a rule, whatever else matches.

Matching is a case-insensitive "contains" test against the sender (display
name and address) or the subject, in the Inbox of every account. All rules are
combined into one query per inbox, so adding rules costs almost nothing.
Matches are moved to that account's Trash, so a mistake is recoverable until
the trash is emptied.

## Command line

```
~/.local/share/mail-cleanup/mail-cleanup.sh [--rules|--junk|--trash|--all|--senders] [--dry-run] [--quiet]
```

`--dry-run` prints what would be erased, per account. The CLI path needs
Automation permission for whatever terminal you run it from; emptying the
trash additionally needs Accessibility for that terminal.

## Why is there an app bundle?

Apple Mail's scripting dictionary cannot empty the trash: `delete` on a message
already in the trash is a silent no-op. The only route is the Mailbox > Erase
Deleted Items menu, driven through System Events, which requires an
Accessibility grant. Grants are tied to a code-signed bundle, so the script is
compiled into `Mail Cleanup.app` and everything that erases goes through it.

## Troubleshooting

- **Nothing happens and the log shows no "Starting" line**: the app did not run
  its script. Rebuild with `~/.local/share/mail-cleanup/build-app.sh`.
- **"Not authorized to send Apple events to Mail" (-1743) with no prompt**:
  the app's signature is broken (for example after hand-editing Info.plist).
  `build-app.sh` re-signs; run it again.
- **"Mail Cleanup is not allowed assistive access"**: add or re-add the app
  under Accessibility. Every rebuild invalidates the old grant.
- Reset a stuck grant: `tccutil reset Accessibility com.mail-cleanup.app` (or
  `tccutil reset AppleEvents com.mail-cleanup.app`), then grant again.
- Watch macOS decide: `log stream --predicate 'process == "tccd" OR process == "applet"'`.

## Development

```bash
uv run --with pytest pytest      # or: pip install pytest && pytest
shellcheck install.sh uninstall.sh build-app.sh mail-cleanup.sh
./install.sh                     # re-install your working copy
```

## Uninstall

```bash
./uninstall.sh            # or: mail-cleanup-uninstall && brew uninstall mail-cleanup
```

Removes the agents, the app and the install prefix. Your rules file and logs
are left alone.

## License

MIT
