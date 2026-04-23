# Microsoft Teams CLI for Claude Code

A CDP-based command-line tool that lets Claude Code agents — and humans — **read messages, send replies, search history, and AI-analyze chats** from Microsoft Teams desktop, without Graph API permissions or admin consent.

> **macOS only.** Depends on AppleScript and Quartz for sidebar navigation.

---

## How It Works

```
Teams desktop (WebView2)
        │
        ▼  CDP WebSocket (port 9229)
   teams.py ──► read / send / search
        │
        ▼  AppleScript + Quartz
   Sidebar scan + chat navigation
        │
        ▼  Anthropic API (optional)
   AI analysis: TODOs + reply suggestions
```

1. **`teams_launch.sh`** relaunches Teams with `--remote-debugging-port=9229`
2. **AppleScript** walks the Teams accessibility tree to find chat coordinates
3. **Quartz mouse events** click the target chat
4. **CDP JavaScript injection** extracts rendered message text from the WebView
5. **`Input.insertText` + `Input.dispatchKeyEvent`** types and sends messages

No Graph API tokens. No admin consent. No screen capture.

---

## Requirements

| Requirement | Notes |
|---|---|
| macOS 13+ | AppleScript + Quartz only available on macOS |
| Microsoft Teams desktop | WebView2-based (the current default) |
| Python 3.9+ | |
| [Claude Code](https://claude.ai/code) | For the `/teams` skill |
| `ANTHROPIC_API_KEY` | Only needed for `analyze` / `unread --analyze` |

---

## Install

```bash
git clone https://github.com/kokjohn0824/teams-reader ~/dev/teams-reader
cd ~/dev/teams-reader
bash install.sh
```

The installer:
1. Runs `python3 -m pip install -r requirements.txt` (`websockets`, `anthropic`)
2. Generates `~/.claude/skills/teams/SKILL.md` from the template (with correct absolute paths)
3. Makes `teams_launch.sh` executable

After install, **restart Claude Code** (or run `/reload`) so it picks up the new `/teams` skill.

---

## One-time Setup: Launch Teams with CDP

```bash
~/dev/teams-reader/teams_launch.sh
```

This quits and relaunches Teams with `--remote-debugging-port=9229`. Run it **once after each Teams restart**. If Teams is already running with CDP, the script is a no-op.

---

## Configuration

Add to `~/.zshrc` (or `~/.bashrc`):

```bash
export TEAMS_MY_NAME="Your Display Name"   # Your name as it appears in Teams
export TEAMS_CDP_PORT=9229                 # Change if 9229 conflicts with something else
export ANTHROPIC_API_KEY="sk-ant-..."      # Required for analyze / unread --analyze
```

`TEAMS_MY_NAME` is used to filter @mention scans and personalize AI analysis. Optional but recommended.

---

## Usage

### In Claude Code (after install)

```
/teams 讀取 Alice 的最新訊息
/teams 傳送給 Project X：「已收到，下午處理」
/teams 分析所有未讀訊息
/teams 搜尋 "SFTP" 在 Project X 群組
```

### CLI

```bash
# Check connection
python3 teams.py status

# Browse chats
python3 teams.py list
python3 teams.py list -f json

# Read messages
python3 teams.py read                            # Current chat, last 20 messages
python3 teams.py read --chat "Alice"             # Specific chat
python3 teams.py read --chat "Project X" --limit 50
python3 teams.py read --chat "Bob" -f json       # JSON output

# Unread chats
python3 teams.py unread                          # All unread chats
python3 teams.py unread --analyze                # + AI summary (requires ANTHROPIC_API_KEY)
python3 teams.py unread --limit 3                # Max 3 chats

# Send a message
python3 teams.py send --chat "Alice" --message "Got it, will check"

# Search
python3 teams.py search --query "SFTP"
python3 teams.py search --query "TODO" --chat "Project X"

# AI analysis: TODOs + suggested replies
python3 teams.py analyze --chat "Alice"
python3 teams.py analyze --chat "Alice" -f json

# @Mention scan across all visible chats
python3 teams.py mentions
python3 teams.py mentions --limit 5
```

### JSON output

All commands support `-f json`:

```bash
python3 teams.py read --chat "Alice" -f json
# { "chat": "Alice", "count": 12, "messages": [{ "time": "...", "sender": "...", "body": "..." }, ...] }

python3 teams.py unread -f json
# { "unread_count": 3, "chats": [...] }

python3 teams.py analyze --chat "Alice" -f json
# { "analysis": "...", "model": "...", "input_tokens": N, "output_tokens": N }
```

---

## macOS-only: Why and What Would Be Needed for Windows

| Component | macOS | Windows equivalent needed |
|---|---|---|
| Sidebar reading | `osascript` + AppleScript AX API | Windows UI Automation (UIA) |
| Chat navigation (click) | `Quartz.CoreGraphics` mouse events | `SendInput` / PowerShell |
| Teams launch with CDP | `open -a "Microsoft Teams"` | `Start-Process` with env var |
| Message reading / sending | CDP WebSocket (cross-platform) | Same |
| AI analysis | Anthropic API (cross-platform) | Same |

**No Windows support currently.** The CDP read/send logic (`teams.py` lines for WebSocket) is already platform-neutral, but the navigation layer (`osascript`, `Quartz`) has no Windows equivalent in this codebase. A Windows port would need to replace those two components; contributions welcome.

---

## Permissions

On first run, macOS may prompt for **Accessibility** permission for your terminal or Claude Code:

> System Settings → Privacy & Security → Accessibility → add your app → toggle ON

This is required for AppleScript to read the Teams sidebar.

---

## For Claude Code Agents

After `install.sh`, Claude Code automatically loads the `/teams` skill from `~/.claude/skills/teams/SKILL.md`.

### Quick reference for agents

| Goal | Command |
|---|---|
| Check if Teams is ready | `python3 teams.py status` |
| List all chats | `python3 teams.py list -f json` |
| Read a chat | `python3 teams.py read --chat "name" -f json` |
| Get all unread | `python3 teams.py unread -f json` |
| Analyze a chat | `python3 teams.py analyze --chat "name" -f json` |
| Send a message | `python3 teams.py send --chat "name" --message "text"` |
| Search | `python3 teams.py search --query "kw" --chat "name" -f json` |
| @Mentions | `python3 teams.py mentions -f json` |

### Common agentic pattern

```bash
# 1. Check all unread chats as structured data
python3 teams.py unread -f json

# 2. Deep-analyze a specific chat
python3 teams.py analyze --chat "Project X" -f json

# 3. Send a reply
python3 teams.py send --chat "Project X" --message "Understood, will handle by EOD"
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TEAMS_MY_NAME` | `""` | Your display name — used for @mention filtering and AI analysis context |
| `TEAMS_CDP_PORT` | `9229` | CDP debug port |
| `ANTHROPIC_API_KEY` | — | Required for `analyze` / `unread --analyze` |

---

## Troubleshooting

**`❌ Teams CDP not available`**
→ Run `bash ~/dev/teams-reader/teams_launch.sh` to relaunch Teams with CDP.

**`❌ Chat 'X' not found`**
→ Scroll down in the Teams sidebar so the chat is visible, then retry.

**`No chats found`**
→ Teams must be on the Chat view (not Calls/Calendar). Run `python3 teams.py list` after switching.

**Accessibility permission error**
→ System Settings → Privacy & Security → Accessibility → add Terminal / Claude Code.

**After Teams auto-update**
→ Re-run `teams_launch.sh`. Teams restarts without CDP after updates.

---

## License

MIT
