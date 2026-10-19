#!/bin/zsh
# Double-click this file to set up MashupStudio.
# It removes macOS quarantine and opens the app.
cd "$(dirname "$0")"
echo "Setting up MashupStudio..."
xattr -cr MashupStudio.app 2>/dev/null
chmod +x MashupStudio.app/Contents/MacOS/MashupStudio
echo ""
echo "NOTE: MashupStudio needs Xcode Command Line Tools to build on first launch."
echo "If you don't have them, run this in Terminal:"
echo "  xcode-select --install"
echo ""
echo "Opening MashupStudio..."
open MashupStudio.app
echo "Done. You can close this window."
