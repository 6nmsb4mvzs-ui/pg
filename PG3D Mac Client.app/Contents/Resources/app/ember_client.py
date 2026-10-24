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
        "User-Agent": "PG3D-Mac-Client/1.77.1",
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

_CHEATS_DEFAULTS = {
    "aimbot": False,
    "triggerbot": False,
    "aim_key": 97,          # F6
    "trigger_key": 98,      # F7
    "aim_key_label": "F6",
    "trigger_key_label": "F7",
    "legitimacy": 1,
    "fov": 12.0,
    "whole_player": False,
    "category_enabled": False,
    "category_delay_ms": 100,
    "category_key_1": 19,
    "category_key_2": 20,
    "category_key_3": 23,
    "category_label_1": "2",
    "category_label_2": "3",
    "category_label_3": "5",
    "gui_visible": True,
    "gui_enabled": False,
}

_CHEATS_ALLOWED = set(_CHEATS_DEFAULTS.keys())


def cheats_config() -> dict:
    _ensure_dirs()
    if CHEATS_FILE.is_file():
        try:
            stored = json.loads(CHEATS_FILE.read_text(encoding="utf-8"))
            merged = dict(_CHEATS_DEFAULTS)
            merged.update({k: v for k, v in stored.items() if k in _CHEATS_ALLOWED})
            return {"ok": True, **merged}
        except Exception:
            pass
    return {"ok": True, **_CHEATS_DEFAULTS}


def save_cheats(config: dict) -> dict:
    _ensure_dirs()
    clean = {k: v for k, v in config.items() if k in _CHEATS_ALLOWED}
    current = cheats_config()
    current.update(clean)
    current.pop("ok", None)
    CHEATS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return {"ok": True, **current}


def write_aim_config(config_path: str, cheats: dict, fps_target: int = -1):
    """Write the config file in the format the PG3DAimAutoFire dylib reads."""
    lines = [
        f"enabled=1",
        f"aim_enabled={1 if cheats.get('aimbot') else 0}",
        f"auto_fire={1 if cheats.get('triggerbot') else 0}",
        f"fov={cheats.get('fov', 12.0):.3f}",
        f"smoothing=6.000",
        f"max_degrees_per_second=90.000",
        f"fire_acquire_ms=0.000",
        f"fire_hold_ms=50.000",
        f"fire_gap_ms=15.000",
        f"fire_max_distance=500.000",
        f"aim_key={cheats.get('aim_key', 97)}",
        f"trigger_key={cheats.get('trigger_key', 98)}",
        f"aim_modifiers=0",
        f"trigger_modifiers=0",
        f"aim_label={cheats.get('aim_key_label', 'F6')}",
        f"trigger_label={cheats.get('trigger_key_label', 'F7')}",
        f"feedback_visible={1 if cheats.get('gui_visible', True) else 0}",
        f"legitimacy={cheats.get('legitimacy', 1)}",
        f"fps_unlock=0",
        f"fps_target={fps_target}",
        f"whole_player={1 if cheats.get('whole_player') else 0}",
        f"category_enabled={1 if cheats.get('category_enabled') else 0}",
        f"category_switch_delay_ms={cheats.get('category_delay_ms', 100)}",
        f"category_key_1={cheats.get('category_key_1', 19)}",
        f"category_key_2={cheats.get('category_key_2', 20)}",
        f"category_key_3={cheats.get('category_key_3', 23)}",
        f"category_label_1={cheats.get('category_label_1', '2')}",
        f"category_label_2={cheats.get('category_label_2', '3')}",
        f"category_label_3={cheats.get('category_label_3', '5')}",
    ]
    Path(config_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
