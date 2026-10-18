#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/6nmsb4mvzs-ui/pg.git"
BRANCH="claude/determined-hypatia-u0kykj"
INSTALL_DIR="$HOME/Ashvale"

GOLD='\033[0;33m'
DIM='\033[0;90m'
RED='\033[0;31m'
RESET='\033[0m'

echo ""
echo -e "${GOLD}  ╔═══════════════════════════════╗${RESET}"
echo -e "${GOLD}  ║         A S H V A L E         ║${RESET}"
echo -e "${GOLD}  ║  A land consumed by ash and    ║${RESET}"
echo -e "${GOLD}  ║          silence.              ║${RESET}"
echo -e "${GOLD}  ╚═══════════════════════════════╝${RESET}"
echo ""

if ! command -v git &>/dev/null; then
    echo -e "${RED}  git not found — install Xcode Command Line Tools:${RESET}"
    echo -e "${RED}  xcode-select --install${RESET}"
    echo ""; read -rp "  press enter to close."; exit 1
fi

if [[ -d "$INSTALL_DIR/game/project.godot" ]] || [[ -f "$INSTALL_DIR/game/project.godot" ]]; then
    echo -e "${DIM}  updating...${RESET}"
    cd "$INSTALL_DIR"
    git pull origin "$BRANCH" --ff-only 2>/dev/null || true
else
    echo -e "${DIM}  installing to ~/Ashvale ...${RESET}"
    git clone -b "$BRANCH" --single-branch --depth 1 "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
exec ./play.sh
