#!/bin/bash
# install.sh — teams-reader installer
# Registers the /teams Claude Code skill using the current directory as install path.

set -e

INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$HOME/.claude/skills/teams"

echo "📦 teams-reader installer"
echo "   Install directory : $INSTALL_DIR"
echo "   Skill destination : $SKILL_DIR/SKILL.md"
echo ""

# 1. Python deps
echo "⬇️  Installing Python dependencies..."
python3 -m pip install -r "$INSTALL_DIR/requirements.txt" -q
echo "   ✅ Dependencies installed"

# 2. Generate SKILL.md from template
mkdir -p "$SKILL_DIR"
sed "s|{{INSTALL_DIR}}|$INSTALL_DIR|g" "$INSTALL_DIR/SKILL.md.template" > "$SKILL_DIR/SKILL.md"
echo "   ✅ Skill registered at $SKILL_DIR/SKILL.md"

# 3. Make launch script executable
chmod +x "$INSTALL_DIR/teams_launch.sh"

echo ""
echo "✅ Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Set your name (optional, used for @mention scan and AI analysis):"
echo "     echo 'export TEAMS_MY_NAME=\"Your Display Name\"' >> ~/.zshrc"
echo ""
echo "  2. Launch Teams with CDP support (once per Teams restart):"
echo "     $INSTALL_DIR/teams_launch.sh"
echo ""
echo "  3. Restart Claude Code (so it picks up the new skill):"
echo "     Quit and reopen Claude Code, or run /reload in the CLI"
echo ""
echo "  4. In Claude Code, use the /teams skill:"
echo "     /teams read the latest message from Alice"
echo "     /teams send to Bob: 'Got it, will check'"
echo "     /teams summarize unread chats"
