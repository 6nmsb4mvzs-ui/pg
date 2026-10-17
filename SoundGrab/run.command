#!/bin/bash
# Double-click this file to build and launch SoundGrab.
cd "$(dirname "$0")"

# Xcode CLI tools
if ! xcode-select -p &>/dev/null; then
    echo "Installing Xcode Command Line Tools (one-time)..."
    xcode-select --install
    echo "Re-run this after the install finishes."
    read -n1 -rsp "Press any key to close..."
    exit 0
fi

# Homebrew
if ! command -v brew &>/dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv 2>/dev/null)"
fi

# Dependencies
for dep in yt-dlp ffmpeg; do
    if ! command -v "$dep" &>/dev/null; then
        echo "Installing $dep..."
        brew install "$dep"
    fi
done

# Build
echo "Building SoundGrab..."
swift build -c release --quiet 2>&1

if [ $? -ne 0 ]; then
    echo ""
    echo "Build failed. Retrying with verbose output..."
    swift build -c release
    read -n1 -rsp "Press any key to close..."
    exit 1
fi

# Launch
echo "Launching..."
.build/release/SoundGrab &
disown
exit 0
