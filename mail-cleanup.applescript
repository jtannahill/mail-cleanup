-- A compiled applet never invokes an `on run argv` handler (it silently runs
-- nothing), so arguments arrive through the MAIL_CLEANUP_ARGS environment
-- variable instead. mail-cleanup.sh sets it for the osascript path; the app is
-- launched with `open --env MAIL_CLEANUP_ARGS=...`.
on run
  set mode to "all"
  set dryRun to false
  set quiet to false

  set argv to my parseArgs()
  repeat with i from 1 to count of argv
    set anArg to item i of argv
    if anArg is "--junk" then set mode to "junk"
    if anArg is "--trash" then set mode to "trash"
    if anArg is "--all" then set mode to "all"
    if anArg is "--rules" then set mode to "rules"
    if anArg is "--senders" then set mode to "senders"
    if anArg is "--dry-run" then set dryRun to true
    if anArg is "--quiet" then set quiet to true
    if anArg is "-h" or anArg is "--help" then
      return "Apple Mail cleanup across all accounts." & linefeed & linefeed & "Usage:" & linefeed & "  mail-cleanup.sh [--rules|--junk|--trash|--all] [--dry-run] [--quiet]" & linefeed & linefeed & "Options:" & linefeed & "  --rules     Trash inbox mail matching ~/.config/mail-cleanup/rules.txt" & linefeed & "  --senders   Print the sender of every inbox message (for Top Senders)" & linefeed & "  --junk      Erase junk/spam in all accounts" & linefeed & "  --trash     Erase deleted/trash items in all accounts" & linefeed & "  --all       Erase both (default)" & linefeed & "  --dry-run   Report counts without erasing" & linefeed & "  --quiet     Log only; no stdout summary" & linefeed & "  -h, --help  Show this help"
    end if
  end repeat

  if mode is "senders" then return my dumpSenders()

  set logPath to (POSIX path of (path to home folder)) & "Library/Logs/mail-cleanup.log"
  my rotateLog(logPath)
  my writeLog(logPath, "--- Starting mail cleanup (mode=" & mode & ", dry_run=" & dryRun & ") ---")

  set rulesCount to 0
  set junkCount to 0
  set trashCount to 0
  set statusCode to 0

  -- Rules run first: matched inbox mail is moved to the trash, which the
  -- trash pass below then erases for good.
  if mode is "rules" or mode is "all" then
    set r to my runCleanup("rules", "Rules", dryRun, quiet, logPath)
    set rulesCount to item 1 of r
    if item 2 of r is not 0 then set statusCode to item 2 of r
  end if

  -- Junk runs before trash on purpose. Deleting a junk message moves it to the
  -- account's trash rather than erasing it, so the trash pass is what actually
  -- clears those messages off the disk.
  if mode is "junk" or mode is "all" then
    set r to my runCleanup("junk", "Junk", dryRun, quiet, logPath)
    set junkCount to item 1 of r
    if item 2 of r is not 0 and statusCode is 0 then set statusCode to item 2 of r
  end if

  if mode is "trash" or mode is "all" then
    set r to my runCleanup("trash", "Trash", dryRun, quiet, logPath)
    set trashCount to item 1 of r
    if item 2 of r is not 0 and statusCode is 0 then set statusCode to item 2 of r
  end if

  my writeLog(logPath, "--- Cleanup complete (rules: " & rulesCount & ", junk: " & junkCount & ", trash: " & trashCount & ") ---")
  -- Only the CLI (osascript) gets a non-zero exit. Inside the applet an
  -- uncaught error becomes a blocking alert the user has to dismiss, and the
  -- exit status is invisible to `open -W` anyway. The log carries the detail.
  if statusCode is not 0 and my runningFromCli() then
    error "Mail cleanup incomplete" number statusCode
  end if
end run

on runningFromCli()
  try
    return (system attribute "MAIL_CLEANUP_CLI") is "1"
  on error
    return false
  end try
end runningFromCli

on parseArgs()
  set rawArgs to ""
  try
    set rawArgs to system attribute "MAIL_CLEANUP_ARGS"
  end try
  if rawArgs is "" then return {}
  set AppleScript's text item delimiters to space
  set parts to text items of rawArgs
  set AppleScript's text item delimiters to ""
  set argv to {}
  repeat with p in parts
    if (p as text) is not "" then set end of argv to (p as text)
  end repeat
  return argv
end parseArgs

on rotateLog(logPath)
  -- Keep the log to the most recent 2000 lines.
  do shell script "mkdir -p " & quoted form of (do shell script "dirname " & quoted form of logPath) & "; " & ¬
    "if [ -f " & quoted form of logPath & " ] && [ $(wc -l < " & quoted form of logPath & ") -gt 5000 ]; then " & ¬
    "tail -n 2000 " & quoted form of logPath & " > " & quoted form of (logPath & ".tmp") & " && " & ¬
    "mv " & quoted form of (logPath & ".tmp") & " " & quoted form of logPath & "; fi"
end rotateLog

on writeLog(logPath, message)
  set stamp to do shell script "date '+%Y-%m-%d %H:%M:%S'"
  do shell script "echo " & quoted form of (stamp & " " & message) & " >> " & quoted form of logPath
end writeLog

on sayLine(quiet, message)
  if quiet then return
  log message
end sayLine

on targetsFor(kind)
  if kind is "junk" then
    return {"Junk", "Spam", "Junk Email"}
  else
    return {"Trash", "Deleted Messages", "Deleted Items", "Bin"}
  end if
end targetsFor

-- Rules live in ~/.config/mail-cleanup/rules.txt, one per line:
--   from:<text>      sender (display name or address) contains <text>
--   subject:<text>   subject contains <text>
-- Matching is case-insensitive. Blank lines and lines starting with # are
-- ignored. Returns a list of {kind, text}.
on rulesPath()
  return (POSIX path of (path to home folder)) & ".config/mail-cleanup/rules.txt"
end rulesPath

-- MAIL_CLEANUP_RULES_OVERRIDE (newline-separated rules) replaces the file, so
-- the menubar can preview a rule before saving it.
on loadRules()
  set ruleList to {}
  set raw to ""
  try
    set raw to system attribute "MAIL_CLEANUP_RULES_OVERRIDE"
  end try
  if raw is "" then
    set rp to my rulesPath()
    try
      set raw to do shell script "cat " & quoted form of rp & " 2>/dev/null || true"
    end try
  end if
  repeat with aLine in paragraphs of raw
    set t to my trimText(aLine as text)
    if t is not "" and t does not start with "#" then
      set olderDays to 0
      -- Optional trailing qualifier: "older:30d" limits the rule to mail
      -- received more than N days ago.
      if t contains " older:" then
        set AppleScript's text item delimiters to " older:"
        set parts to text items of t
        set AppleScript's text item delimiters to ""
        set t to my trimText(item 1 of parts)
        set q to my trimText(item 2 of parts)
        if q ends with "d" then set q to text 1 thru -2 of q
        try
          set olderDays to q as integer
        end try
      end if
      if t starts with "from:" then
        set v to my trimText(text 6 thru -1 of t)
        if v is not "" then set end of ruleList to {"from", v, olderDays}
      else if t starts with "subject:" then
        set v to my trimText(text 9 thru -1 of t)
        if v is not "" then set end of ruleList to {"subject", v, olderDays}
      else if t starts with "keep:" then
        set v to my trimText(text 6 thru -1 of t)
        if v is not "" then set end of ruleList to {"keep", v, 0}
      end if
    end if
  end repeat
  return ruleList
end loadRules

on hasTrashRules(ruleList)
  repeat with aRule in ruleList
    if (item 1 of aRule) is not "keep" then return true
  end repeat
  return false
end hasTrashRules

on trimText(t)
  repeat while t is not "" and (character 1 of t is space or character 1 of t is tab)
    if (length of t) is 1 then return ""
    set t to text 2 thru -1 of t
  end repeat
  repeat while t is not "" and (character -1 of t is space or character -1 of t is tab or character -1 of t is return)
    if (length of t) is 1 then return ""
    set t to text 1 thru -2 of t
  end repeat
  return t
end trimText

-- One `whose` query per inbox covering every rule at once. Mail evaluates a
-- compound filter far faster than one round trip per rule, and the cost stays
-- flat as the rule list grows. The filter is built from the rules at runtime
-- and compiled once into a script object with a matchIn(mailbox) handler.
on escapeForSource(t)
  set AppleScript's text item delimiters to "\\"
  set parts to text items of t
  set AppleScript's text item delimiters to "\\\\"
  set t to parts as text
  set AppleScript's text item delimiters to "\""
  set parts to text items of t
  set AppleScript's text item delimiters to "\\\""
  set t to parts as text
  set AppleScript's text item delimiters to ""
  return t
end escapeForSource

on buildMatcher(ruleList)
  set clauses to {}
  set keeps to {}
  set cutoffLines to ""
  set n to 0
  repeat with aRule in ruleList
    set kindOf to item 1 of aRule
    set needle to my escapeForSource(item 2 of aRule)
    set olderDays to item 3 of aRule
    if kindOf is "keep" then
      set end of keeps to "sender contains \"" & needle & "\""
    else
      set n to n + 1
      if kindOf is "from" then
        set c to "sender contains \"" & needle & "\""
      else
        set c to "subject contains \"" & needle & "\""
      end if
      if olderDays > 0 then
        -- `whose` needs a concrete date, so compute it in the generated
        -- handler rather than inline in the filter.
        set cutoffLines to cutoffLines & "set cutoff" & n & " to (current date) - " & olderDays & " * days" & linefeed
        set c to "(" & c & " and date received < cutoff" & n & ")"
      end if
      set end of clauses to c
    end if
  end repeat
  set AppleScript's text item delimiters to " or "
  set filterText to "(" & (clauses as text) & ")"
  if (count of keeps) > 0 then
    set filterText to filterText & " and not (" & (keeps as text) & ")"
  end if
  set AppleScript's text item delimiters to ""
  set src to "script matcher" & linefeed & ¬
    "on matchIn(mb)" & linefeed & cutoffLines & ¬
    "tell application \"Mail\" to return every message of mb whose (" & filterText & ")" & linefeed & ¬
    "end matchIn" & linefeed & ¬
    "end script" & linefeed & "return matcher"
  return run script src
end buildMatcher

-- Prints one line per inbox message: the sender string. The menubar tallies
-- these for its Top Senders list; AppleScript has no fast dictionary.
on dumpSenders()
  set AppleScript's text item delimiters to linefeed
  set out to {}
  tell application "Mail"
    if not running then launch
    repeat with acct in every account
      repeat with inboxBox in (every mailbox of acct whose name is "INBOX" or name is "Inbox")
        set out to out & (sender of every message of inboxBox)
      end repeat
    end repeat
  end tell
  set txt to out as text
  set AppleScript's text item delimiters to ""
  return txt
end dumpSenders

-- Returns {totalCount, detailText}; detail lines are account/Inbox/count.
on countRuleMatches()
  set ruleList to my loadRules()
  if not my hasTrashRules(ruleList) then return {0, ""}
  set matcher to my buildMatcher(ruleList)
  set totalCount to 0
  set details to ""
  tell application "Mail"
    if not running then launch
    repeat with acct in every account
      set acctName to name of acct
      repeat with inboxBox in (every mailbox of acct whose name is "INBOX" or name is "Inbox")
        set n to count of (matcher's matchIn(inboxBox))
        if n > 0 then
          set totalCount to totalCount + n
          set details to details & acctName & tab & "Inbox" & tab & n & linefeed
        end if
      end repeat
    end repeat
  end tell
  return {totalCount, details}
end countRuleMatches

-- Moves matched inbox messages to the account trash (Mail's `delete`).
on eraseRuleMatches(logPath)
  set ruleList to my loadRules()
  if not my hasTrashRules(ruleList) then return 0
  set matcher to my buildMatcher(ruleList)
  set failures to 0
  tell application "Mail"
    if not running then launch
    repeat with acct in every account
      set acctName to name of acct
      repeat with inboxBox in (every mailbox of acct whose name is "INBOX" or name is "Inbox")
        try
          set hits to matcher's matchIn(inboxBox)
          if (count of hits) > 0 then delete hits
        on error errMsg
          set failures to failures + 1
          my writeLog(logPath, "ERROR: " & acctName & "/Inbox: " & errMsg)
        end try
      end repeat
    end repeat
  end tell
  return failures
end eraseRuleMatches

-- Returns {totalCount, detailText} where each detail line is account/mailbox/count.
on countMailboxes(kind)
  if kind is "rules" then return my countRuleMatches()
  set targetNames to my targetsFor(kind)
  set totalCount to 0
  set details to ""
  tell application "Mail"
    if not running then launch
    repeat with acct in every account
      set acctName to name of acct
      repeat with mbox in every mailbox of acct
        if targetNames contains (name of mbox) then
          set msgCount to count of messages of mbox
          if msgCount > 0 then
            set totalCount to totalCount + msgCount
            set details to details & acctName & tab & (name of mbox) & tab & msgCount & linefeed
          end if
        end if
      end repeat
    end repeat
  end tell
  return {totalCount, details}
end countMailboxes

-- Junk erases through Mail's own scripting dictionary: no System Events, no
-- Accessibility grant, and Mail never steals focus. Note this MOVES the
-- messages to the account trash, which the trash pass then clears.
on eraseJunk(logPath)
  set targetNames to my targetsFor("junk")
  set failures to 0
  tell application "Mail"
    if not running then launch
    repeat with acct in every account
      set acctName to name of acct
      repeat with mbox in every mailbox of acct
        if targetNames contains (name of mbox) then
          if (count of messages of mbox) > 0 then
            try
              delete (every message of mbox)
            on error errMsg
              set failures to failures + 1
              my writeLog(logPath, "ERROR: " & acctName & "/" & (name of mbox) & ": " & errMsg)
            end try
          end if
        end if
      end repeat
    end repeat
  end tell
  return failures
end eraseJunk

-- Trash cannot be emptied through the scripting dictionary. `delete` means
-- "move to trash", so for a message already in the trash it is a silent no-op.
-- Permanently erasing requires the Mailbox > Erase Deleted Items menu, which
-- needs Accessibility. The menu carries two variants: "In All Accounts..."
-- opens a confirmation sheet, while the hidden "In All Accounts" (no ellipsis)
-- erases outright. Prefer the latter and fall back to the former.
on eraseTrashViaMenu(logPath)
  set priorApp to ""
  try
    tell application "System Events"
      set priorApp to name of first application process whose frontmost is true
    end tell
  end try

  set failures to 0
  -- Check the grant up front. Without it the menu click below fails anyway,
  -- and this avoids dragging Mail to the front for nothing.
  set trusted to false
  try
    tell application "System Events" to set trusted to UI elements enabled
  end try
  if not trusted then
    my writeLog(logPath, "ERROR: trash erase skipped: Mail Cleanup is not allowed assistive access. Opening System Settings > Privacy & Security > Accessibility; enable Mail Cleanup there, then run again.")
    try
      do shell script "open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility'"
    end try
    return 1
  end if
  try
    tell application "Mail" to activate
    tell application "System Events"
      tell process "Mail"
        set eraseMenu to menu 1 of menu item "Erase Deleted Items" of menu "Mailbox" of menu bar 1
        if exists menu item "In All Accounts" of eraseMenu then
          click menu item "In All Accounts" of eraseMenu
        else
          click menu item "In All Accounts…" of eraseMenu
          repeat 20 times
            if exists sheet 1 of window 1 then exit repeat
            delay 0.25
          end repeat
          if exists sheet 1 of window 1 then
            click button "Erase" of sheet 1 of window 1
          end if
        end if
      end tell
    end tell
  on error errMsg
    set failures to 1
    my writeLog(logPath, "ERROR: trash erase failed: " & errMsg)
  end try

  -- Give Mail a moment to expunge before the caller re-counts.
  delay 2

  if priorApp is not "" and priorApp is not "Mail" then
    try
      tell application priorApp to activate
    end try
  end if
  return failures
end eraseTrashViaMenu

on eraseMailboxes(kind, logPath)
  if kind is "rules" then return my eraseRuleMatches(logPath)
  if kind is "junk" then
    return my eraseJunk(logPath)
  else
    return my eraseTrashViaMenu(logPath)
  end if
end eraseMailboxes

on logDetails(details, quiet, logPath)
  repeat with detailLine in paragraphs of details
    if (detailLine as text) is not "" then
      set AppleScript's text item delimiters to tab
      set parts to text items of (detailLine as text)
      set AppleScript's text item delimiters to ""
      if (count of parts) is 3 then
        set detailText to "  " & (item 1 of parts) & "/" & (item 2 of parts) & ": " & (item 3 of parts)
        my writeLog(logPath, detailText)
        my sayLine(quiet, detailText)
      end if
    end if
  end repeat
end logDetails

-- Mail expunges asynchronously, so counting straight after the erase reports
-- the old total. Poll until the mailboxes drain or the count stops falling.
on waitForDrain(kind)
  set remaining to item 1 of my countMailboxes(kind)
  set stableFor to 0
  repeat 30 times
    if remaining is 0 then return 0
    delay 1
    set current to item 1 of my countMailboxes(kind)
    if current is remaining then
      set stableFor to stableFor + 1
      if stableFor is 5 then return current
    else
      set stableFor to 0
    end if
    set remaining to current
  end repeat
  return remaining
end waitForDrain

on runCleanup(kind, label, dryRun, quiet, logPath)
  set countResult to my countMailboxes(kind)
  set beforeCount to item 1 of countResult
  set details to item 2 of countResult

  if beforeCount is 0 then
    my writeLog(logPath, label & ": none found")
    my sayLine(quiet, label & ": none found")
    return {0, 0}
  end if

  if dryRun then
    my writeLog(logPath, label & ": would erase " & beforeCount & " messages")
    my sayLine(quiet, label & ": would erase " & beforeCount & " messages")
    my logDetails(details, quiet, logPath)
    return {beforeCount, 0}
  end if

  my logDetails(details, quiet, logPath)
  set failures to my eraseMailboxes(kind, logPath)

  set remaining to my waitForDrain(kind)
  set removed to beforeCount - remaining

  if remaining > 0 then
    my writeLog(logPath, label & ": erased " & removed & "/" & beforeCount & " messages (" & remaining & " remain)")
    my sayLine(quiet, label & ": erased " & removed & "/" & beforeCount & " messages (" & remaining & " remain)")
    if failures > 0 then return {beforeCount, 1}
    return {beforeCount, 2}
  end if

  my writeLog(logPath, label & ": erased " & beforeCount & " messages")
  my sayLine(quiet, label & ": erased " & beforeCount & " messages")
  return {beforeCount, 0}
end runCleanup
