#!/usr/bin/env python3
"""
teams.py — Microsoft Teams CLI for Claude Code agents
CDP-based: < 0.1s per read, real-time send, search, analysis

Prerequisites:
  ./teams_launch.sh        # 啟動 Teams with CDP debug port

Usage:
  teams.py status                          # Check connection
  teams.py list [--format json|table]      # List all chats
  teams.py read [--chat X] [--limit N]     # Read messages
  teams.py unread [--limit N] [--analyze]  # Read all unread chats
  teams.py send --chat X --message "Y"     # Send a message
  teams.py search --query X [--chat X]     # Search messages
  teams.py analyze --chat X                # AI analysis (TODOs + replies)
  teams.py mentions [--limit N]            # Check @mentions
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import websockets

# ──────────────────── 設定 ────────────────────

CDP_PORT = int(os.environ.get("TEAMS_CDP_PORT", "9229"))
CDP_URL = f"http://localhost:{CDP_PORT}/json"
TEAMS_DIR = Path(__file__).parent
TEAMS_MY_NAME = os.environ.get("TEAMS_MY_NAME", "")

# ──────────────────── CDP 基礎 ────────────────────

def cdp_targets() -> list[dict]:
    try:
        with urllib.request.urlopen(CDP_URL, timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return []


def is_connected() -> bool:
    return len(cdp_targets()) > 0


async def cdp_eval(ws_url: str, js: str, timeout: float = 10.0):
    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024, open_timeout=5) as ws:
        cmd = {"id": 1, "method": "Runtime.evaluate",
               "params": {"expression": js, "returnByValue": True, "awaitPromise": True}}
        await ws.send(json.dumps(cmd))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        return resp.get("result", {}).get("result", {}).get("value")


async def cdp_send_cmd(ws_url: str, method: str, params: dict = None):
    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024, open_timeout=5) as ws:
        cmd = {"id": 1, "method": method, "params": params or {}}
        await ws.send(json.dumps(cmd))
        return json.loads(await ws.recv())


def get_chat_target(chat_name: str | None = None) -> dict | None:
    """Find the CDP page target for a given chat (or current chat if None)."""
    targets = cdp_targets()
    if chat_name:
        # Exact then partial match
        for t in targets:
            if t.get("type") == "page" and chat_name in t.get("title", ""):
                return t
    # Fallback: any Chat | page
    return next((t for t in targets
                 if t.get("type") == "page" and "Chat |" in t.get("title", "")), None)


def get_main_page_target() -> dict | None:
    """Find the main Teams app page (with nav bar)."""
    targets = cdp_targets()
    pages = [t for t in targets if t.get("type") == "page"]
    # Prefer pages with content
    return next((t for t in pages if len(t.get("title", "")) > 3), None)


# ──────────────────── 側欄（AX API）────────────────────

_OUTLINE_SCRIPT = """
tell application "System Events"
    tell process "MSTeams"
        set nl to ASCII character 10
        set w to window 1
        set n1 to group 1 of w
        set n2 to group 1 of n1
        set n3 to group 2 of n2
        set n4 to group 2 of n3
        set n5 to group 2 of n4
        set n6 to group 1 of n5
        set n7 to group 1 of n6
        set n8 to group 1 of n7
        set n9 to group 1 of n8
        set uie to UI element 1 of n9
        set n10 to group 1 of uie
        set n11 to group 1 of n10
        set n12 to group 1 of n11
        set n13 to group 4 of n12
        set n14 to group 1 of n13
        set n15 to group 1 of n14
        set theOutline to outline 1 of n15
        set buf to ""
        set rowCnt to count of every row of theOutline
        repeat with rowIdx from 1 to rowCnt
            set parentRow to row rowIdx of theOutline
            set parentKids to every UI element of parentRow
            if (count of parentKids) >= 2 then
                set innerGrp to UI element 2 of parentRow
                set innerKids to every UI element of innerGrp
                repeat with kidIdx from 1 to count of innerKids
                    set kid to UI element kidIdx of innerGrp
                    set kr to ""
                    try
                        set kr to role of kid
                    end try
                    if kr = "AXRow" then
                        set kt to ""
                        try
                            set kt to title of kid
                        end try
                        set kpos to position of kid
                        set ksz to size of kid
                        set cx to (item 1 of kpos) + (item 1 of ksz) / 2
                        set cy to (item 2 of kpos) + (item 2 of ksz) / 2
                        set buf to buf & kt & "|||" & cx & "," & cy & nl
                    end if
                end repeat
            end if
        end repeat
        buf
    end tell
end tell
"""


def get_sidebar_chats() -> list[dict]:
    r = subprocess.run(["osascript", "-e", _OUTLINE_SCRIPT],
                       capture_output=True, text=True, timeout=20)
    chats = []
    for line in r.stdout.strip().splitlines():
        if "|||" not in line:
            continue
        title, coords = line.split("|||", 1)
        try:
            cx, cy = map(float, coords.strip().split(","))
        except ValueError:
            continue
        chats.append({"title": title.strip(), "cx": cx, "cy": cy})
    return chats


def extract_chat_name(row_title: str) -> str:
    name = re.sub(r"^Unread\s+message\s+", "", row_title)
    name = re.sub(r"^Unread\s+", "", name)
    name = re.sub(r"^(Chat|Group chat|Meeting chat)\s+", "", name)
    name = re.sub(
        r"\s+(Available|Away|Busy|Offline|In a call|Do not disturb"
        r"|Has context menu|Has pinned messages|Has external participants"
        r"|Muted|Draft|In a meeting).*$", "", name)
    return name.strip()


def find_chat_in_sidebar(name: str, chats: list[dict]) -> dict | None:
    def score(c):
        n = extract_chat_name(c["title"])
        if n == name: return 0
        if n.startswith(name): return 1
        is_direct = "Group chat" not in c["title"] and "Meeting chat" not in c["title"]
        if name in n: return 2 if is_direct else 3
        return 99
    candidates = [c for c in chats if name in extract_chat_name(c["title"])]
    return min(candidates, key=score) if candidates else None


# ──────────────────── 導航 ────────────────────

async def ensure_chat_view():
    """Switch Teams to Chat view via CDP."""
    page = get_main_page_target()
    if not page:
        return
    js = """(function(){
        const btn = Array.from(document.querySelectorAll('button,[role="tab"]'))
            .find(e => e.getAttribute('aria-label')?.startsWith('Chat'));
        if(btn){ btn.click(); return 'ok'; } return 'nf';
    })()"""
    try:
        await cdp_eval(page["webSocketDebuggerUrl"], js)
        await asyncio.sleep(1.5)
    except Exception:
        pass


def navigate_cgevent(cx: float, cy: float):
    try:
        from Quartz.CoreGraphics import (
            CGEventCreateMouseEvent, CGEventPost,
            kCGHIDEventTap, kCGEventMouseMoved,
            kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGMouseButtonLeft,
        )
        subprocess.run(["osascript", "-e",
                        'tell application "Microsoft Teams" to activate'],
                       capture_output=True, timeout=3)
        time.sleep(0.8)
        for ev in [kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp]:
            e = CGEventCreateMouseEvent(None, ev, (cx, cy), kCGMouseButtonLeft)
            CGEventPost(kCGHIDEventTap, e)
            time.sleep(0.05)
    except ImportError:
        subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to tell process "MSTeams"'
             f' to click at {{{int(cx)}, {int(cy)}}}'],
            capture_output=True, timeout=5)


def wait_for_target(chat_name: str, timeout: int = 12) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t in cdp_targets():
            if t.get("type") == "page" and chat_name in t.get("title", ""):
                return t
        time.sleep(0.4)
    # fallback
    return next((t for t in cdp_targets()
                 if t.get("type") == "page" and "Chat |" in t.get("title", "")), None)


async def navigate_to_chat(name: str) -> dict | None:
    """Navigate Teams to a named chat; return the CDP target."""
    await ensure_chat_view()
    chats = get_sidebar_chats()
    chat = find_chat_in_sidebar(name, chats)
    if not chat:
        return None
    navigate_cgevent(chat["cx"], chat["cy"])
    return wait_for_target(name)


# ──────────────────── 訊息讀取 ────────────────────

_MSG_JS = """
(function() {
    const JUNK = /^(Translate|Edit|Delete|Reply|More message options|Edited|Like|Type a message|has context menu|Last read)$/i;
    const REACTION = /^\\d+ (Like|Love|Wow|Sad|Angry|Haha|reaction)/;
    const QUOTE = /^(Begin|End) (quote|reference|Reference)/;
    const TIME_RE = /^(\\w+day )?\\d+:\\d+ (AM|PM)$|^\\d+\\/\\d+\\/\\d+ \\d+:\\d+ (AM|PM)$|^\\d+\\/\\d+ \\d+:\\d+ (AM|PM)$/;

    const items = document.querySelectorAll('[data-tid="chat-pane-item"]');
    const messages = [];

    items.forEach(item => {
        const timeEl = item.querySelector('time[datetime]');
        const timeText = timeEl ? timeEl.innerText.trim() : '';
        const datetime = timeEl ? timeEl.getAttribute('datetime') : '';

        // 送信者："X by Y" aria-label パターン
        let sender = '';
        const ariaEl = item.querySelector('[aria-label]');
        if (ariaEl) {
            const m = ariaEl.getAttribute('aria-label').match(/ by ([^,|\\n]+?)(?:\\s|$)/);
            if (m) sender = m[1].trim();
        }
        // フォールバック：時刻の直前にある短いテキストノード
        if (!sender && timeEl) {
            let sib = timeEl.previousElementSibling;
            while (sib) {
                const t = sib.innerText ? sib.innerText.trim() : '';
                if (t && t.length < 60 && !TIME_RE.test(t) && !JUNK.test(t)) {
                    sender = t; break;
                }
                sib = sib.previousElementSibling;
            }
        }

        // メッセージ本体：不要な行を除去
        const rawText = item.innerText ? item.innerText.trim() : '';
        const bodyLines = rawText.split('\\n').map(l => l.trim()).filter(l => {
            if (!l || l === sender || l === timeText) return false;
            if (JUNK.test(l) || REACTION.test(l) || QUOTE.test(l)) return false;
            if (TIME_RE.test(l)) return false;
            // "X by Y" プレビュー行を除去
            if (/^.+ by [^\\n]+$/.test(l) && l.length < 300) return false;
            return true;
        });
        const body = bodyLines.join('\\n').trim();
        if (!body || body.length < 2) return;

        messages.push({ time: timeText, datetime, sender, body });
    });

    return messages;
})()
"""


async def read_messages(ws_url: str) -> list[dict]:
    raw = await cdp_eval(ws_url, _MSG_JS)
    return raw if raw else []


# ──────────────────── 訊息傳送 ────────────────────

async def send_message(ws_url: str, text: str) -> bool:
    """Type and send a message in the current Teams chat via CDP."""
    # 1. Focus the compose box
    focus_js = """
(function(){
    const box = document.querySelector('[data-tid="ckeditor"]') ||
                document.querySelector('[aria-label="Type a message"]') ||
                document.querySelector('[role="textbox"]');
    if (!box) return false;
    box.focus();
    box.click();
    return true;
})()
"""
    ok = await cdp_eval(ws_url, focus_js)
    if not ok:
        return False

    await asyncio.sleep(0.3)

    # 2. Insert text
    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024, open_timeout=5) as ws:
        await ws.send(json.dumps({
            "id": 1, "method": "Input.insertText", "params": {"text": text}
        }))
        await ws.recv()

    await asyncio.sleep(0.3)

    # 3. Click the send button.
    # Teams default keybinding is Cmd+Enter (not plain Enter), so dispatching
    # a bare Enter keydown adds a newline instead of sending.
    send_js = """
(function(){
    const btn = document.querySelector('[data-tid="sendMessageCommands-send"]') ||
                document.querySelector('[aria-label="Send"]') ||
                document.querySelector('button[aria-label*="Send"]');
    if (!btn || btn.disabled) return false;
    btn.click();
    return true;
})()
"""
    sent = await cdp_eval(ws_url, send_js)
    return bool(sent)


# ──────────────────── Claude 分析 ────────────────────

def analyze_with_claude(chats_data: list[dict]) -> dict:
    import anthropic
    client = anthropic.Anthropic()

    chat_texts = []
    for chat in chats_data:
        lines = [f"=== 聊天室：{chat['name']} ==="]
        for m in chat["messages"][-30:]:
            lines.append(f"[{m['time']}] {m['sender']}: {m['body'][:500]}")
        chat_texts.append("\n".join(lines))

    perspective = f"（以{TEAMS_MY_NAME}的角度）" if TEAMS_MY_NAME else ""
    prompt = f"""分析以下 Microsoft Teams 聊天記錄{perspective}：

{chr(10).join(chat_texts)}

請用繁體中文提供：
## [聊天室名稱]
### 待辦事項
- （列出需要處理的事項）

### 建議回應
- 針對「[訊息摘要]」：建議回應「...」

### 重要資訊
- （關鍵決策、技術細節、需追蹤事項）
"""
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return {
        "analysis": resp.content[0].text,
        "model": resp.model,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }


# ──────────────────── 出力格式化 ────────────────────

def print_messages(messages: list[dict], limit: int = 0, fmt: str = "table"):
    display = messages[-limit:] if limit > 0 else messages
    if fmt == "json":
        print(json.dumps(display, ensure_ascii=False, indent=2))
        return
    for m in display:
        print(f"\n[{m.get('time','?')}] {m.get('sender','?')}")
        body = m.get("body", "")
        print(f"  {body[:300]}")


def print_chats(chats: list[dict], fmt: str = "table"):
    if fmt == "json":
        output = [{"name": extract_chat_name(c["title"]),
                   "title": c["title"],
                   "unread": "Unread" in c["title"]} for c in chats]
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    for c in chats:
        unread = "🔴" if "Unread" in c["title"] else "  "
        name = extract_chat_name(c["title"])
        print(f"{unread} {name}")


# ──────────────────── サブコマンド ────────────────────

def cmd_status(args):
    """Check Teams + CDP connection status."""
    targets = cdp_targets()
    if not targets:
        print("❌ Teams CDP not available")
        print("   Run: ./teams_launch.sh")
        sys.exit(1)
    running = subprocess.run(["pgrep", "-x", "MSTeams"], capture_output=True)
    print(f"✅ Teams running: {'yes' if running.returncode == 0 else 'no'}")
    print(f"✅ CDP port {CDP_PORT}: connected")
    print(f"   Page targets: {len([t for t in targets if t.get('type')=='page'])}")
    chat = get_chat_target()
    if chat:
        print(f"   Current chat: {chat['title']}")


def cmd_list(args):
    """List all available chats."""
    if not is_connected():
        print("❌ Run ./teams_launch.sh first"); sys.exit(1)
    asyncio.run(ensure_chat_view())
    chats = get_sidebar_chats()
    if not chats:
        print("No chats found (Teams may need to be on Chat view)")
        sys.exit(66)
    print_chats(chats, fmt=args.format)


async def _read_async(args):
    if not is_connected():
        print("❌ Run ./teams_launch.sh first"); sys.exit(1)

    target = None
    if args.chat:
        # 先找已開啟的 CDP target
        target = get_chat_target(args.chat)
        if not target:
            target = await navigate_to_chat(args.chat)
        if not target:
            print(f"❌ Chat '{args.chat}' not found")
            sys.exit(1)
        await asyncio.sleep(0.3)
    else:
        target = get_chat_target()

    if not target:
        print("❌ No chat open"); sys.exit(1)

    chat_name = target["title"].replace("Chat | ", "").replace(" | Microsoft Teams", "")
    messages = await read_messages(target["webSocketDebuggerUrl"])

    if args.format == "json":
        print(json.dumps({
            "chat": chat_name,
            "count": len(messages),
            "messages": messages[-args.limit:] if args.limit else messages
        }, ensure_ascii=False, indent=2))
    else:
        print(f"📨 {chat_name} — {len(messages)} messages")
        print_messages(messages, limit=args.limit or 0, fmt=args.format)


def cmd_read(args):
    asyncio.run(_read_async(args))


async def _unread_async(args):
    if not is_connected():
        print("❌ Run ./teams_launch.sh first"); sys.exit(1)
    await ensure_chat_view()
    all_chats = get_sidebar_chats()
    unread = [c for c in all_chats if "Unread" in c["title"]]

    if not unread:
        print("✅ No unread messages")
        if args.format == "json":
            print(json.dumps({"unread": [], "count": 0}))
        return

    limit = args.limit or len(unread)
    results = []
    for chat in unread[:limit]:
        name = extract_chat_name(chat["title"])
        print(f"  → Reading: {name}", file=sys.stderr)
        navigate_cgevent(chat["cx"], chat["cy"])
        target = wait_for_target(name, timeout=10)
        if not target:
            continue
        await asyncio.sleep(0.3)
        messages = await read_messages(target["webSocketDebuggerUrl"])
        results.append({"name": name, "title": chat["title"],
                         "read_at": datetime.now().isoformat(), "messages": messages})
        await asyncio.sleep(0.5)

    if args.format == "json":
        print(json.dumps({"unread_count": len(results), "chats": results},
                         ensure_ascii=False, indent=2))
    else:
        total = sum(len(r["messages"]) for r in results)
        print(f"\n✅ {len(results)} unread chats, {total} messages total")
        for r in results:
            print(f"\n{'─'*40}\n📬 {r['name']} ({len(r['messages'])} msg)")
            print_messages(r["messages"], limit=5)

    if args.analyze and results:
        print("\n🤖 Analyzing with Claude...", file=sys.stderr)
        result = analyze_with_claude(results)
        print("\n" + "=" * 60)
        print(result["analysis"])
        print("=" * 60)


def cmd_unread(args):
    asyncio.run(_unread_async(args))


async def _send_async(args):
    if not is_connected():
        print("❌ Run ./teams_launch.sh first"); sys.exit(1)

    # 先找已開啟的 CDP target，避免不必要的導航
    target = get_chat_target(args.chat)
    if not target:
        target = await navigate_to_chat(args.chat)
    if not target:
        print(f"❌ Chat '{args.chat}' not found"); sys.exit(1)
    await asyncio.sleep(0.3)

    ok = await send_message(target["webSocketDebuggerUrl"], args.message)
    if ok:
        print(f"✅ Sent to {args.chat}: {args.message[:80]}")
    else:
        print("❌ Failed to send (compose box not found)"); sys.exit(1)


def cmd_send(args):
    asyncio.run(_send_async(args))


async def _search_async(args):
    if not is_connected():
        print("❌ Run ./teams_launch.sh first"); sys.exit(1)

    if args.chat:
        target = await navigate_to_chat(args.chat)
    else:
        target = get_chat_target()

    if not target:
        print("❌ No chat available"); sys.exit(1)

    messages = await read_messages(target["webSocketDebuggerUrl"])
    q = args.query.lower()
    hits = [m for m in messages
            if q in m.get("body", "").lower() or q in m.get("sender", "").lower()]

    if args.format == "json":
        print(json.dumps({"query": args.query, "hits": len(hits), "results": hits},
                         ensure_ascii=False, indent=2))
    else:
        print(f"🔍 '{args.query}' — {len(hits)} results")
        print_messages(hits, limit=args.limit or 0)


def cmd_search(args):
    asyncio.run(_search_async(args))


async def _analyze_async(args):
    if not is_connected():
        print("❌ Run ./teams_launch.sh first"); sys.exit(1)

    if args.chat:
        target = get_chat_target(args.chat) or await navigate_to_chat(args.chat)
        await asyncio.sleep(0.3)
    else:
        target = get_chat_target()

    if not target:
        print("❌ No chat available"); sys.exit(1)

    chat_name = target["title"].replace("Chat | ", "").replace(" | Microsoft Teams", "")
    messages = await read_messages(target["webSocketDebuggerUrl"])

    if not messages:
        print("❌ No messages to analyze"); sys.exit(66)

    print(f"🤖 Analyzing {len(messages)} messages from '{chat_name}'...", file=sys.stderr)
    result = analyze_with_claude([{"name": chat_name, "messages": messages}])

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["analysis"])


def cmd_analyze(args):
    asyncio.run(_analyze_async(args))


async def _mentions_async(args):
    if not is_connected():
        print("❌ Run ./teams_launch.sh first"); sys.exit(1)

    await ensure_chat_view()
    all_chats = get_sidebar_chats()

    mentions = []
    # Scan each chat for @mentions
    for chat in all_chats:
        name = extract_chat_name(chat["title"])
        navigate_cgevent(chat["cx"], chat["cy"])
        target = wait_for_target(name, timeout=8)
        if not target:
            continue
        await asyncio.sleep(0.3)
        messages = await read_messages(target["webSocketDebuggerUrl"])
        for m in messages:
            if (TEAMS_MY_NAME and TEAMS_MY_NAME in m.get("body", "")) or "@" in m.get("body", ""):
                mentions.append({**m, "chat": name})
        await asyncio.sleep(0.4)

    limit = args.limit or len(mentions)
    display = mentions[-limit:]

    if args.format == "json":
        print(json.dumps({"count": len(mentions), "mentions": display},
                         ensure_ascii=False, indent=2))
    else:
        print(f"📣 {len(mentions)} mentions found")
        for m in display:
            print(f"\n[{m.get('time')}] {m.get('chat')} — {m.get('sender')}")
            print(f"  {m.get('body','')[:200]}")


def cmd_mentions(args):
    asyncio.run(_mentions_async(args))


# ──────────────────── CLI 入口 ────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="teams.py",
        description="Microsoft Teams CLI for Claude Code agents"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="Check Teams + CDP connection")

    # list
    p = sub.add_parser("list", help="List all chats")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"])

    # read
    p = sub.add_parser("read", help="Read messages from a chat")
    p.add_argument("--chat", help="Chat name (partial match)")
    p.add_argument("--limit", type=int, default=20, help="Number of messages (default: 20)")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"])

    # unread
    p = sub.add_parser("unread", help="Read all unread chats")
    p.add_argument("--limit", type=int, default=None, help="Max chats to read")
    p.add_argument("--analyze", action="store_true", help="Run Claude analysis")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"])

    # send
    p = sub.add_parser("send", help="Send a message")
    p.add_argument("--chat", required=True, help="Chat name")
    p.add_argument("--message", required=True, help="Message text")

    # search
    p = sub.add_parser("search", help="Search messages")
    p.add_argument("--query", required=True, help="Search term")
    p.add_argument("--chat", help="Limit to specific chat")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("-f", "--format", default="table", choices=["table", "json"])

    # analyze
    p = sub.add_parser("analyze", help="AI analysis of a chat (TODOs + suggested replies)")
    p.add_argument("--chat", help="Chat name")
    p.add_argument("-f", "--format", default="table", choices=["table", "json"])

    # mentions
    p = sub.add_parser("mentions", help="Find @mentions across chats")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("-f", "--format", default="table", choices=["table", "json"])

    return parser


COMMANDS = {
    "status": cmd_status,
    "list": cmd_list,
    "read": cmd_read,
    "unread": cmd_unread,
    "send": cmd_send,
    "search": cmd_search,
    "analyze": cmd_analyze,
    "mentions": cmd_mentions,
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
