#!/bin/zsh
# PG3D Mac Client launcher.
# Double-clicking the app runs this. It installs the app into Applications on
# first run, sets up everything it needs by itself, then opens the client.
set -u
setopt NULL_GLOB

VERSION="1.74.1"
PORT=38513
APP_ID="pg3d-mac-client-v$VERSION"
SOURCE_BASE="$(cd "$(dirname "$0")" && pwd)"
APP_BUNDLE="$(cd "$SOURCE_BASE/../../.." && pwd)"
APP_NAME="PG3D Mac Client.app"
SUPPORT="$HOME/Library/Application Support/PG3D Mac Client"
BUNDLE_CACHE="$SUPPORT/bundle-v$VERSION"
RUNTIME="$SUPPORT/python-runtime"
STANDALONE="$SUPPORT/python-standalone"
LOG="$SUPPORT/launcher.log"
PIDFILE="$SUPPORT/server-v$VERSION.pid"
SHELL_PIDFILE="$SUPPORT/shell-v$VERSION.pid"
URL="http://127.0.0.1:$PORT/?v=$VERSION"
PY_RELEASE="20241219"
PY_VERSION="3.12.8"
mkdir -p "$SUPPORT"

# Keep the log from growing forever.
if [[ -f "$LOG" ]] && (( $(/usr/bin/stat -f %z "$LOG") > 5000000 )); then
  /usr/bin/tail -c 1000000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
touch "$LOG"
echo "[$(date '+%Y-%m-%dT%H:%M:%S')] launch v$VERSION from $APP_BUNDLE args: $*" >>"$LOG"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"PG3D Client\"" >/dev/null 2>&1 || true
}
fail() {
  local choice
  choice="$(/usr/bin/osascript -e "button returned of (display dialog \"$1\" with title \"PG3D Client\" buttons {\"Show log\", \"OK\"} default button \"OK\" with icon caution)" 2>/dev/null)"
  [[ "$choice" == "Show log" ]] && /usr/bin/open -R "$LOG"
  exit 1
}
stop_running() {
  local STOPPED=()
  for PF in "$SUPPORT"/server-v*.pid "$SUPPORT"/shell-v*.pid "$SUPPORT"/overlay*.pid; do
    [[ -f "$PF" ]] || continue
    OPID="$(cat "$PF" 2>/dev/null || true)"
    if [[ -n "$OPID" && "$OPID" != "$PPID" ]] && /bin/kill -0 "$OPID" 2>/dev/null; then
      /bin/kill "$OPID" 2>/dev/null || true
      STOPPED+=("$OPID")
    fi
    /bin/rm -f "$PF"
  done
  # Old windows of this app that are still open (e.g. the copy that was just updated):
  # every client process except the one starting now ($PPID is this copy's launcher).
  for OPID in $(/usr/bin/pgrep -f "PG3D Mac Client.app/Contents/MacOS/" 2>/dev/null); do
    [[ "$OPID" == "$PPID" || "$OPID" == "$$" ]] && continue
    /bin/kill "$OPID" 2>/dev/null && STOPPED+=("$OPID")
  done
  /bin/sleep 0.4
  # Anything that ignored the polite quit within 3 s is stopped for good (an old copy
  # left running kept its icon in the Dock next to the new one).
  for i in {1..15}; do
    local LEFT=0
    for OPID in "${STOPPED[@]}"; do /bin/kill -0 "$OPID" 2>/dev/null && LEFT=1; done
    (( LEFT )) || break
    /bin/sleep 0.2
  done
  for OPID in "${STOPPED[@]}"; do /bin/kill -9 "$OPID" 2>/dev/null || true; done
}

POST_INSTALL=""
JUST_SIGNED=""
for arg in "$@"; do
  [[ "$arg" == "--installed" ]] && POST_INSTALL=1
  [[ "$arg" == "--signed" ]] && JUST_SIGNED=1
done
SIGN_NAME="PG3D Mac Client Local Signing"
signed_ok() { /usr/bin/codesign -dvv "$1" 2>&1 | /usr/bin/grep -q "Authority=$SIGN_NAME"; }

# ---------------------------------------------------------------- self-install
# Anything not already in an Applications folder (Downloads, Desktop, or the
# temporary location macOS runs downloaded apps from) gets installed, and the
# installed copy is opened instead. The download is cleaned up afterwards.
case "$APP_BUNDLE" in
  /Applications/*|"$HOME"/Applications/*) ;;
  *)
    DEST_DIR="/Applications"
    if [[ ! -w "$DEST_DIR" ]]; then DEST_DIR="$HOME/Applications"; mkdir -p "$DEST_DIR"; fi
    DEST="$DEST_DIR/$APP_NAME"
    notify "Installing PG3D Client…"
    stop_running
    rm -rf "$DEST.installing"
    if /usr/bin/ditto "$APP_BUNDLE" "$DEST.installing" >>"$LOG" 2>&1; then
      # You already approved this copy when opening it, so the installed copy
      # doesn't need to be checked again.
      /usr/bin/xattr -dr com.apple.quarantine "$DEST.installing" 2>/dev/null || true
      # The same signing identity in every version keeps macOS permissions (Screen
      # Recording, Downloads) from being asked again after updates.
      /bin/zsh "$SOURCE_BASE/sign_app.sh" "$DEST.installing" >>"$LOG" 2>&1
      rm -rf "$DEST.previous"
      [[ -e "$DEST" ]] && mv "$DEST" "$DEST.previous"
      if mv "$DEST.installing" "$DEST"; then
        rm -rf "$DEST.previous"
        echo "Installed to $DEST" >>"$LOG"
        /usr/bin/open -n "$DEST" --args --installed
        exit 0
      fi
      [[ -e "$DEST.previous" ]] && mv "$DEST.previous" "$DEST"
    fi
    echo "Install into $DEST_DIR failed; running from $APP_BUNDLE" >>"$LOG"
    ;;
esac

# ---------------------------------------------------------------- stable signature
# A copy that isn't signed with the local identity yet (an update installed by an older
# version) signs itself once and restarts, so macOS sees the same app as before.
SIGN_FAILED="$SUPPORT/signing/failed-v$VERSION"
if [[ -z "$JUST_SIGNED" && ! -f "$SIGN_FAILED" ]] && ! signed_ok "$APP_BUNDLE"; then
  if /bin/zsh "$SOURCE_BASE/sign_app.sh" "$APP_BUNDLE" >>"$LOG" 2>&1 && signed_ok "$APP_BUNDLE"; then
    echo "Signed $APP_BUNDLE with the local identity; restarting it" >>"$LOG"
    /usr/bin/open -n "$APP_BUNDLE" --args --signed ${POST_INSTALL:+--installed}
    exit 0
  fi
  mkdir -p "$SUPPORT/signing"; touch "$SIGN_FAILED"
  echo "Local signing failed; continuing unsigned" >>"$LOG"
fi

# ---------------------------------------------------------------- already open?
FAST_SHELL_PID="$(cat "$SHELL_PIDFILE" 2>/dev/null || true)"
if [[ -z "$POST_INSTALL" && -n "$FAST_SHELL_PID" ]] && /bin/kill -0 "$FAST_SHELL_PID" 2>/dev/null; then
  if /usr/bin/curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" 2>/dev/null | /usr/bin/grep -q "$APP_ID"; then
    /bin/kill -USR1 "$FAST_SHELL_PID" 2>/dev/null && exit 0
  fi
fi
stop_running

# ---------------------------------------------------------------- app files
STAMP="$(/usr/bin/find "$SOURCE_BASE" -type f -not -path '*/__pycache__/*' -exec /usr/bin/stat -f '%m %z %N' {} + 2>/dev/null | /usr/bin/shasum | /usr/bin/cut -d' ' -f1)"
if [[ ! -f "$BUNDLE_CACHE/.bundle-stamp" || "$(cat "$BUNDLE_CACHE/.bundle-stamp" 2>/dev/null)" != "$STAMP" ]]; then
  rm -rf "$BUNDLE_CACHE"
  /usr/bin/ditto "$SOURCE_BASE" "$BUNDLE_CACHE" >>"$LOG" 2>&1 || fail "PG3D Client couldn't prepare its files."
  echo "$STAMP" > "$BUNDLE_CACHE/.bundle-stamp"
  for OLD in "$SUPPORT"/bundle-v*; do [[ "$OLD" == "$BUNDLE_CACHE" ]] || rm -rf "$OLD"; done
fi
BASE="$BUNDLE_CACHE"
chmod +x "$BASE/fps_unlocker/build/launch-game" "$BASE/fps_unlocker/build/PG3DFPSUnlock.dylib" 2>/dev/null || true

# ---------------------------------------------------------------- Python
py_ok() { [[ -x "$1" ]] && "$1" -c 'import sys, venv, ssl; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }
find_python() {
  local p
  for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 "$STANDALONE/bin/python3"; do
    py_ok "$p" && { echo "$p"; return 0; }
  done
  # Apple's /usr/bin/python3 only works when the Command Line Tools are
  # installed; otherwise touching it pops up an install prompt, so check first.
  if /usr/bin/xcode-select -p >/dev/null 2>&1 && py_ok /usr/bin/python3; then echo /usr/bin/python3; return 0; fi
  return 1
}
download_python() {
  local triple tmp
  [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" == "1" ]] && triple="aarch64-apple-darwin" || triple="x86_64-apple-darwin"
  tmp="$SUPPORT/python-download"
  rm -rf "$tmp"; mkdir -p "$tmp"
  /usr/bin/curl -fL --retry 3 --connect-timeout 20 -o "$tmp/python.tar.gz" \
    "https://github.com/astral-sh/python-build-standalone/releases/download/$PY_RELEASE/cpython-$PY_VERSION%2B$PY_RELEASE-$triple-install_only.tar.gz" >>"$LOG" 2>&1 || return 1
  /usr/bin/tar -xzf "$tmp/python.tar.gz" -C "$tmp" >>"$LOG" 2>&1 || return 1
  rm -rf "$STANDALONE"; mv "$tmp/python" "$STANDALONE"; rm -rf "$tmp"
  py_ok "$STANDALONE/bin/python3"
}

FIRST_RUN=""
PY="$(find_python)"
if [[ -z "$PY" ]]; then
  FIRST_RUN=1
  notify "Setting up PG3D Client for the first time. This takes about a minute."
  download_python || fail "PG3D Client couldn't download the Python runtime it needs. Check your internet connection and open it again."
  PY="$STANDALONE/bin/python3"
fi

# Rebuild the private runtime if it's missing or its base Python moved (for example after a Homebrew upgrade).
if [[ ! -x "$RUNTIME/bin/python" ]] || ! "$RUNTIME/bin/python" -c 'import sys' >/dev/null 2>&1; then
  [[ -z "$FIRST_RUN" ]] && notify "Setting up PG3D Client. This takes about a minute."
  FIRST_RUN=1
  rm -rf "$RUNTIME"
  "$PY" -m venv "$RUNTIME" >>"$LOG" 2>&1 || fail "PG3D Client couldn't create its runtime."
fi
VPY="$RUNTIME/bin/python"

if ! "$VPY" -c 'import Cocoa, Quartz, WebKit, certifi' >/dev/null 2>&1; then
  [[ -z "$FIRST_RUN" ]] && notify "Updating PG3D Client components…"
  "$VPY" -m pip install --disable-pip-version-check -q --upgrade pip >>"$LOG" 2>&1 || true
  "$VPY" -m pip install --disable-pip-version-check -q pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-WebKit certifi >>"$LOG" 2>&1 \
    || fail "PG3D Client couldn't download its interface components. Check your internet connection and open it again."
fi
# Standalone Python has no system certificates; point it at certifi's bundle.
export SSL_CERT_FILE="$("$VPY" -c 'import certifi; print(certifi.where())' 2>/dev/null)"

# Cursor tools install in the background and never block startup. Packages built for the
# other architecture (the client used to run as Intel on Apple Silicon) are reinstalled.
PROBE="$("$VPY" -c 'import UnityPy; from PIL import Image; import numpy' 2>&1)"
if [[ $? -ne 0 ]]; then
  FORCE=()
  [[ "$PROBE" == *"incompatible architecture"* ]] && FORCE=(--force-reinstall --no-cache-dir)
  ( "$VPY" -m pip install --disable-pip-version-check -q "${FORCE[@]}" "UnityPy==1.25.3" "Pillow>=10,<13" "numpy>=1.24,<3" >>"$LOG" 2>&1 ||
    "$VPY" -m pip install --disable-pip-version-check -q "${FORCE[@]}" "UnityPy>=1.25,<2" "Pillow>=10,<13" >>"$LOG" 2>&1 ) &
fi

# WebSocket client for the Socket tab (background, non-blocking).
if ! "$VPY" -c 'import websocket; import cryptography' >/dev/null 2>&1; then
  ( "$VPY" -m pip install --disable-pip-version-check -q websocket-client cryptography >>"$LOG" 2>&1 ) &
fi

# Game sound in clips uses ScreenCaptureKit (macOS 13+); clips stay silent without it.
if [[ "$(/usr/bin/sw_vers -productVersion | /usr/bin/cut -d. -f1)" -ge 13 ]] && ! "$VPY" -c 'import ScreenCaptureKit, CoreMedia, AVFoundation' >/dev/null 2>&1; then
  ( "$VPY" -m pip install --disable-pip-version-check -q pyobjc-framework-ScreenCaptureKit pyobjc-framework-CoreMedia pyobjc-framework-AVFoundation >>"$LOG" 2>&1 ) &
fi

# ---------------------------------------------------------------- start
export PG3D_APP_BUNDLE="$APP_BUNDLE"
export PG3D_POST_INSTALL="$POST_INSTALL"
nohup "$VPY" "$BASE/server.py" "$PORT" >>"$LOG" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PIDFILE"
for i in {1..150}; do
  if /usr/bin/curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" 2>/dev/null | /usr/bin/grep -q "$APP_ID"; then
    if [[ -n "${PG3D_HANDOFF:-}" ]]; then
      # The app's own executable (launcher/src/pg3d_launcher.c) runs the window by
      # loading Python into itself, so macOS shows the client, not a "Python" app.
      PYINFO="$("$VPY" -c 'import os, site, sys, sysconfig
c = sysconfig.get_config_vars()
libs = [os.path.join(c.get("PYTHONFRAMEWORKPREFIX") or "", c.get("LDLIBRARY") or ""),
        os.path.join(c.get("LIBDIR") or "", c.get("LDLIBRARY") or ""),
        os.path.join(c.get("LIBDIR") or "", "libpython%s.dylib" % c.get("VERSION")),
        os.path.join(sys.base_prefix, "lib", "libpython%s.dylib" % c.get("VERSION")),
        os.path.join(sys.base_prefix, "Python"), os.path.join(sys.base_prefix, "Python3")]
libs = [l for l in libs if os.path.isabs(l)]
print(next((l for l in libs if os.path.isfile(l)), ""))
print(sys.base_prefix)
print(site.getsitepackages()[0])' 2>>"$LOG")"
      PYLIB="$(echo "$PYINFO" | sed -n 1p)"
      if [[ -n "$PYLIB" ]]; then
        { echo "$PYLIB"; echo "$PYINFO" | sed -n 2p; echo "$PYINFO" | sed -n 3p; echo "$VPY"
          echo "$BASE/native_shell.py"; echo "$URL"; echo "$SHELL_PIDFILE"; echo "$LOG"; } > "$PG3D_HANDOFF"
        exit 0
      fi
      echo "[$(date '+%Y-%m-%dT%H:%M:%S')] no shared Python library found; starting the window directly" >>"$LOG"
    fi
    exec "$VPY" "$BASE/native_shell.py" "$URL" "$SHELL_PIDFILE" >>"$LOG" 2>&1
  fi
  /bin/kill -0 "$SERVER_PID" 2>/dev/null || break
  /bin/sleep 0.12
done
/bin/rm -f "$PIDFILE"
fail "PG3D Client didn't start."
