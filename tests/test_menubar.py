"""Unit tests for the pure-Python parts of the menubar: rules file handling,
count parsing and the before/after measurement. No Mail, no AppKit."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def mod(tmp_path, monkeypatch):
    # rumps and PyObjC need a GUI session; stub them so the module imports
    # anywhere (CI, Linux).
    rumps = types.ModuleType("rumps")

    class _Stub:
        def __init__(self, *a, **k):
            self._menuitem = self

        def setToolTip_(self, _):
            pass

    for name in ("App", "MenuItem", "Window"):
        setattr(rumps, name, _Stub)
    rumps.timer = lambda *_: (lambda f: f)
    rumps.alert = lambda *a, **k: 1
    rumps.notification = lambda *a, **k: None
    rumps.quit_application = lambda: None
    monkeypatch.setitem(sys.modules, "rumps", rumps)
    appkit = types.ModuleType("AppKit")
    appkit.NSApplication = _Stub
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    helper = types.ModuleType("PyObjCTools.AppHelper")
    helper.callAfter = lambda f, *a: f(*a)
    pkg = types.ModuleType("PyObjCTools")
    pkg.AppHelper = helper
    monkeypatch.setitem(sys.modules, "PyObjCTools", pkg)
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", helper)

    spec = importlib.util.spec_from_file_location("menubar", ROOT / "mail-cleanup-menubar.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.RULES = tmp_path / "rules.txt"
    return m


def test_parse_counts_reads_all_three(mod):
    text = "Rules: would erase 3 messages\nJunk: none found\nTrash: would erase 12 messages\n"
    assert mod.parse_counts(text) == (3, 0, 12)


def test_parse_counts_ignores_detail_lines(mod):
    text = "  Work/Inbox: 99\nJunk: would erase 1 messages\n"
    assert mod.parse_counts(text) == (0, 1, 0)


def test_load_rules_skips_comments_blanks_and_junk(mod):
    mod.RULES.write_text("# c\n\nfrom:a@b.c\nbogus line\nsubject:Hi there\n")
    assert mod.load_rules() == ["from:a@b.c", "subject:Hi there"]


def test_load_rules_missing_file(mod):
    assert mod.load_rules() == []


def test_add_rule_creates_file_with_header(mod):
    mod.add_rule("from", "x@y.z")
    body = mod.RULES.read_text()
    assert body.startswith("#")
    assert body.endswith("from:x@y.z\n")
    assert mod.load_rules() == ["from:x@y.z"]


def test_add_rule_appends_newline_when_missing(mod):
    mod.RULES.write_text("from:a@b.c")
    mod.add_rule("subject", "News")
    assert mod.load_rules() == ["from:a@b.c", "subject:News"]


def test_remove_rule_only_removes_exact_line(mod):
    mod.RULES.write_text("from:a@b.c\nfrom:a@b.com\n")
    mod.remove_rule("from:a@b.c")
    assert mod.load_rules() == ["from:a@b.com"]


def test_remove_rule_missing_file_is_noop(mod):
    mod.remove_rule("from:nobody")
    assert not mod.RULES.exists()


@pytest.mark.parametrize(
    "mode,expected",
    [("--rules", 3), ("--junk", 4), ("--trash", 5), ("--all", 12)],
)
def test_measure_per_mode(mod, mode, expected):
    assert mod.MailCleanupMenuBar._measure(mode, 3, 4, 5) == expected


def test_measure_treats_none_as_zero(mod):
    assert mod.MailCleanupMenuBar._measure("--all", None, None, None) == 0


DRY_RUN = """Rules: would erase 3 messages
  Work/Inbox: 3
Junk: none found
Trash: would erase 12 messages
  Work/Trash: 10
  Home/Deleted Messages: 2
"""


def test_parse_details_sections_and_accounts(mod):
    assert mod.parse_details(DRY_RUN) == [
        ("Rules", "Work", "Inbox", 3),
        ("Trash", "Work", "Trash", 10),
        ("Trash", "Home", "Deleted Messages", 2),
    ]


def test_parse_details_empty(mod):
    assert mod.parse_details("Junk: none found\n") == []


@pytest.mark.parametrize(
    "sender,address",
    [
        ("Jane Doe <Jane@Example.com>", "jane@example.com"),
        ("bare@example.com", "bare@example.com"),
        ("  <x@y.z>  ", "x@y.z"),
    ],
)
def test_sender_address(mod, sender, address):
    assert mod.sender_address(sender) == address


def test_top_senders_tallies_by_address(mod):
    dump = "A <a@x.com>\nB <b@x.com>\na@x.com\n\nA2 <A@X.COM>\n"
    assert mod.top_senders(dump) == [("a@x.com", 3), ("b@x.com", 1)]


def test_top_senders_limit(mod):
    dump = "\n".join(f"u{i}@x.com" for i in range(20))
    assert len(mod.top_senders(dump, limit=5)) == 5


def test_load_rules_accepts_keep(mod):
    mod.RULES.write_text("keep:boss@work.com\nfrom:x@y.z older:30d\n")
    assert mod.load_rules() == ["keep:boss@work.com", "from:x@y.z older:30d"]
