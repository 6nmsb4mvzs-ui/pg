#!/bin/zsh
# MacroClicker launcher.
# Follows the same pattern as PG3D Mac Client: finds/installs Python,
# creates a venv, installs dependencies, starts the Flask server, opens the UI.
set -u
setopt NULL_GLOB

VERSION="1.0.0"
PORT=39100
SOURCE_BASE="$(cd "$(dirname "$0")" && pwd)"
APP_BUNDLE="$(cd "$SOURCE_BASE/../../.." && pwd)"
SUPPORT="$HOME/Library/Application Support/MacroClicker"
RUNTIME="$SUPPORT/python-runtime"
STANDALONE="$SUPPORT/python-standalone"
LOG="$SUPPORT/launcher.log"
PIDFILE="$SUPPORT/server.pid"
PY_RELEASE="20241219"
PY_VERSION="3.12.8"
mkdir -p "$SUPPORT"

# Trim log if too large.
if [[ -f "$LOG" ]] && (( $(/usr/bin/stat -f %z "$LOG") > 5000000 )); then
  /usr/bin/tail -c 1000000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
touch "$LOG"
echo "[$(date '+%Y-%m-%dT%H:%M:%S')] launch v$VERSION from $APP_BUNDLE" >>"$LOG"

notify() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"MacroClicker\"" >/dev/null 2>&1 || true
}
fail() {
  /usr/bin/osascript -e "display dialog \"$1\" with title \"MacroClicker\" buttons {\"OK\"} default button \"OK\" with icon caution" 2>/dev/null
  exit 1
}

# Stop any existing instance.
if [[ -f "$PIDFILE" ]]; then
  OPID="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "$OPID" ]] && /bin/kill -0 "$OPID" 2>/dev/null; then
    /bin/kill "$OPID" 2>/dev/null || true
    /bin/sleep 0.5
  fi
  /bin/rm -f "$PIDFILE"
fi

# ---------------------------------------------------------------- Python
py_ok() { [[ -x "$1" ]] && "$1" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; }
find_python() {
  local p
  for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 "$STANDALONE/bin/python3"; do
    py_ok "$p" && { echo "$p"; return 0; }
  done
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

PY="$(find_python)"
if [[ -z "$PY" ]]; then
  notify "Setting up MacroClicker for the first time..."
  download_python || fail "MacroClicker couldn't download Python. Check your internet."
  PY="$STANDALONE/bin/python3"
fi

# Build venv if needed.
if [[ ! -x "$RUNTIME/bin/python" ]] || ! "$RUNTIME/bin/python" -c 'import sys' >/dev/null 2>&1; then
  notify "Setting up MacroClicker..."
  rm -rf "$RUNTIME"
  "$PY" -m venv "$RUNTIME" >>"$LOG" 2>&1 || fail "MacroClicker couldn't create its runtime."
fi
VPY="$RUNTIME/bin/python"

# Install Flask + pyobjc for input automation.
if ! "$VPY" -c 'import flask, Quartz' >/dev/null 2>&1; then
  notify "Installing MacroClicker components..."
  "$VPY" -m pip install --disable-pip-version-check -q --upgrade pip >>"$LOG" 2>&1 || true
  "$VPY" -m pip install --disable-pip-version-check -q flask pyobjc-framework-Quartz >>"$LOG" 2>&1 \
    || fail "MacroClicker couldn't install its dependencies."
fi

# ---------------------------------------------------------------- start
export MACROCLICKER_SUPPORT="$SUPPORT"
nohup "$VPY" "$SOURCE_BASE/server.py" "$PORT" >>"$LOG" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PIDFILE"

# Wait for server, then open browser.
for i in {1..100}; do
  if /usr/bin/curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" 2>/dev/null | /usr/bin/grep -q "macroclicker"; then
    /usr/bin/open "http://127.0.0.1:$PORT"
    exit 0
  fi
  /bin/kill -0 "$SERVER_PID" 2>/dev/null || break
  /bin/sleep 0.1
done
/bin/rm -f "$PIDFILE"
fail "MacroClicker didn't start."
