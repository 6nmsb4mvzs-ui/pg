#!/usr/bin/env bash
set -euo pipefail

GODOT_VERSION="4.3-stable"
GODOT_BASE="Godot_v4.3-stable"
PROJECT_DIR="$(cd "$(dirname "$0")/game" && pwd)"
ENGINE_DIR="$(cd "$(dirname "$0")" && pwd)/.engine"

RED='\033[0;31m'
GOLD='\033[0;33m'
DIM='\033[0;90m'
RESET='\033[0m'

header() {
    echo ""
    echo -e "${GOLD}  ╔═══════════════════════════════╗${RESET}"
    echo -e "${GOLD}  ║         A S H V A L E         ║${RESET}"
    echo -e "${GOLD}  ║  A land consumed by ash and    ║${RESET}"
    echo -e "${GOLD}  ║          silence.              ║${RESET}"
    echo -e "${GOLD}  ╚═══════════════════════════════╝${RESET}"
    echo ""
}

find_godot() {
    if [[ -f "$ENGINE_DIR/godot" ]]; then
        echo "$ENGINE_DIR/godot"
        return 0
    fi
    if command -v godot &>/dev/null; then
        echo "godot"
        return 0
    fi
    if [[ "$(uname)" == "Darwin" ]]; then
        local app="/Applications/Godot.app/Contents/MacOS/Godot"
        if [[ -f "$app" ]]; then
            echo "$app"
            return 0
        fi
        local app_mono="/Applications/Godot_mono.app/Contents/MacOS/Godot"
        if [[ -f "$app_mono" ]]; then
            echo "$app_mono"
            return 0
        fi
    fi
    return 1
}

download_godot() {
    local os arch url filename
    os="$(uname)"
    arch="$(uname -m)"

    if [[ "$os" != "Darwin" ]]; then
        if [[ "$os" == "Linux" ]]; then
            if [[ "$arch" == "x86_64" ]]; then
                filename="${GODOT_BASE}_linux.x86_64"
                url="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/${filename}.zip"
            else
                echo -e "${RED}unsupported linux arch: $arch${RESET}"
                exit 1
            fi
        else
            echo -e "${RED}unsupported OS: $os${RESET}"
            echo "download Godot 4.3+ from https://godotengine.org/download"
            exit 1
        fi
    else
        if [[ "$arch" == "arm64" ]]; then
            filename="${GODOT_BASE}_macos.universal"
            url="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/${filename}.zip"
        else
            filename="${GODOT_BASE}_macos.universal"
            url="https://github.com/godotengine/godot/releases/download/${GODOT_VERSION}/${filename}.zip"
        fi
    fi

    mkdir -p "$ENGINE_DIR"
    local zipfile="$ENGINE_DIR/godot.zip"

    echo -e "${DIM}  downloading godot ${GODOT_VERSION}...${RESET}"
    curl -L --progress-bar -o "$zipfile" "$url"

    echo -e "${DIM}  extracting...${RESET}"
    unzip -qo "$zipfile" -d "$ENGINE_DIR"
    rm -f "$zipfile"

    if [[ "$os" == "Darwin" ]]; then
        local app_path="$ENGINE_DIR/Godot.app"
        if [[ -d "$app_path" ]]; then
            ln -sf "$app_path/Contents/MacOS/Godot" "$ENGINE_DIR/godot"
            xattr -dr com.apple.quarantine "$app_path" 2>/dev/null || true
        fi
    else
        local bin
        bin=$(find "$ENGINE_DIR" -maxdepth 1 -name "Godot_*" -type f ! -name "*.zip" | head -1)
        if [[ -n "$bin" ]]; then
            chmod +x "$bin"
            ln -sf "$bin" "$ENGINE_DIR/godot"
        fi
    fi

    if [[ ! -f "$ENGINE_DIR/godot" ]]; then
        echo -e "${RED}  extraction failed — download manually from https://godotengine.org/download${RESET}"
        exit 1
    fi

    echo -e "${DIM}  engine ready.${RESET}"
}

main() {
    header

    local godot_bin
    if godot_bin="$(find_godot)"; then
        echo -e "${DIM}  engine: $godot_bin${RESET}"
    else
        echo -e "${DIM}  no godot found — fetching...${RESET}"
        download_godot
        godot_bin="$ENGINE_DIR/godot"
    fi

    if [[ ! -f "$PROJECT_DIR/project.godot" ]]; then
        echo -e "${RED}  project.godot not found in $PROJECT_DIR${RESET}"
        exit 1
    fi

    echo -e "${DIM}  launching ashvale...${RESET}"
    echo ""

    "$godot_bin" --path "$PROJECT_DIR" "$@"
}

main "$@"
