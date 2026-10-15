#!/bin/bash
set -e

echo "=== SoundGrab Setup ==="
echo ""

# Check for Homebrew
if ! command -v brew &>/dev/null; then
    echo "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Add Homebrew to PATH for Apple Silicon
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi

echo "Installing yt-dlp and ffmpeg..."
brew install yt-dlp ffmpeg 2>/dev/null || brew upgrade yt-dlp ffmpeg 2>/dev/null || true

echo ""
echo "Checking installations:"
echo -n "  yt-dlp:  "; command -v yt-dlp && yt-dlp --version || echo "NOT FOUND"
echo -n "  ffmpeg:  "; command -v ffmpeg && ffmpeg -version 2>&1 | head -1 || echo "NOT FOUND"

echo ""
echo "=== Setup complete ==="
echo ""
echo "To build and run SoundGrab:"
echo "  1. Open SoundGrab/Package.swift in Xcode"
echo "  2. Select the SoundGrab scheme"
echo "  3. Press Cmd+R to build and run"
echo ""
echo "Or build from the command line:"
echo "  cd SoundGrab && swift build -c release"
echo "  .build/release/SoundGrab"
