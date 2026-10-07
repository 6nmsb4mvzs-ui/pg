"""Ember PG3D API client — proxies emberpg3d.com through the local server."""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor

EMBER_BASE = "https://emberpg3d.com/api"
SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
STATE_DIR = SUPPORT / "state"
EMBER_CREDS_FILE = STATE_DIR / "ember_creds.json"
CHEATS_FILE = STATE_DIR / "cheats.json"

_lock = threading.Lock()
_session_cookies = CookieJar()
_opener = build_opener(HTTPCookieProcessor(_session_cookies))
_logged_in = False


def _ensure_dirs():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _ember_request(method, endpoint, body=None, timeout=15):
    url = EMBER_BASE + endpoint
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "PG3D-Mac-Client/1.76.1",
        "Accept": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            raw = resp.read(4 * 1024 * 1024)
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return {"ok": True, "raw": raw.decode("utf-8", errors="replace")}
    except Exception as e:
        msg = str(e)
        raw_err = None
        if hasattr(e, "read"):
            try:
                raw_err = e.read()
            except Exception:
                pass
        if raw_err:
            try:
                err_body = json.loads(raw_err)
                msg = err_body.get("message") or err_body.get("error") or err_body.get("msg") or str(err_body)
            except (json.JSONDecodeError, ValueError):
                decoded = raw_err.decode("utf-8", errors="replace").strip()
                if decoded:
                    msg = decoded[:300]
        raise RuntimeError(f"Ember: {msg}")


# ── credentials (persistent, stored in Application Support) ──

def save_credentials(username: str, password: str) -> dict:
    _ensure_dirs()
    # Try macOS Keychain first
    ok = _keychain_save(username, password)
    if not ok:
        EMBER_CREDS_FILE.write_text(json.dumps({
            "username": username, "password": password
        }), encoding="utf-8")
    return {"ok": True, "stored": "keychain" if ok else "file"}


def load_credentials() -> dict:
    creds = _keychain_load()
    if creds:
        return {"ok": True, "username": creds[0], "password": creds[1], "source": "keychain"}
    if EMBER_CREDS_FILE.is_file():
        try:
            data = json.loads(EMBER_CREDS_FILE.read_text(encoding="utf-8"))
            return {"ok": True, "username": data["username"], "password": data["password"], "source": "file"}
        except Exception:
            pass
    return {"ok": False, "error": "No saved Ember credentials."}


def _keychain_save(username, password):
    try:
        subprocess.run([
            "security", "delete-generic-password",
            "-s", "PG3D-Mac-Client-Ember", "-a", "ember"
        ], capture_output=True, timeout=5)
    except Exception:
        pass
    try:
        r = subprocess.run([
            "security", "add-generic-password",
            "-s", "PG3D-Mac-Client-Ember", "-a", "ember",
            "-w", json.dumps({"u": username, "p": password}),
            "-U"
        ], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _keychain_load():
    try:
        r = subprocess.run([
            "security", "find-generic-password",
            "-s", "PG3D-Mac-Client-Ember", "-a", "ember", "-w"
        ], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout.strip())
            return data["u"], data["p"]
    except Exception:
        pass
    return None


# ── auth ──

def login(username: str, password: str) -> dict:
    global _logged_in
    result = _ember_request("POST", "/auth/login", {"username": username, "password": password})
    _logged_in = True
    return {"ok": True, "user": result}


def logout() -> dict:
    global _logged_in
    try:
        _ember_request("POST", "/auth/logout")
    except Exception:
        pass
    _logged_in = False
    _session_cookies.clear()
    return {"ok": True}


def me() -> dict:
    return _ember_request("GET", "/auth/me")


# ── accounts ──

def accounts() -> dict:
    return _ember_request("GET", "/game/accounts")


def session() -> dict:
    return _ember_request("GET", "/game/session")


def connect(account_id, method="saved") -> dict:
    body = {"method": method, "account_id": account_id} if method == "saved" else {"method": method}
    return _ember_request("POST", "/game/connect", body)


def disconnect() -> dict:
    return _ember_request("DELETE", "/game/disconnect")


# ── commands ──

def command(action: str, params: dict | None = None) -> dict:
    return _ember_request("POST", "/game/command", {"action": action, "params": params or {}})


def max_progress(options: dict | None = None) -> dict:
    params = {
        "level": True, "level_value": 65,
        "weapons": True, "weapon_parts": False, "weapon_skins": True,
        "profile": True, "wear": True, "armor": True, "wear_skins": True,
        "royale": True, "gadgets": True, "pets": True, "modules": True,
        "lobby": True, "dlc": False, "skip_tutorial": True,
        "skip_br_tutorial": True, "set_paying_user": True,
        "bonus_currency": True,
    }
    if options:
        params.update(options)
    return _ember_request("POST", "/game/command", {"action": "add_everything", "params": params})


def max_progress_status() -> dict:
    return _ember_request("GET", "/game/max-progress")


# ── items ──

def items(category: str) -> dict:
    return _ember_request("GET", f"/game/items/{category}")


# ── cheats config (persistent) ──

def cheats_config() -> dict:
    _ensure_dirs()
    if CHEATS_FILE.is_file():
        try:
            return {"ok": True, **json.loads(CHEATS_FILE.read_text(encoding="utf-8"))}
        except Exception:
            pass
    return {"ok": True, "aimbot": False, "triggerbot": False,
            "aimbot_key": "mouse4", "triggerbot_key": "mouse5"}


def save_cheats(config: dict) -> dict:
    _ensure_dirs()
    allowed = {"aimbot", "triggerbot", "aimbot_key", "triggerbot_key"}
    clean = {k: v for k, v in config.items() if k in allowed}
    CHEATS_FILE.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    return {"ok": True, **clean}
