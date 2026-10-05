#!/usr/bin/env python3
from __future__ import annotations

import atexit
import base64
import copy
import hashlib
import io
import json
import mimetypes
import plistlib
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import Request, urlopen

import importlib

class _LazyModule:
    """Import an optional module only when a feature actually needs it."""
    def __init__(self, module_name):
        self.module_name = module_name
        self._module = None

    def _load(self):
        if self._module is None:
            self._module = importlib.import_module(self.module_name)
        return self._module

    def __getattr__(self, name):
        return getattr(self._load(), name)

UnityPy = _LazyModule("UnityPy")
Image = _LazyModule("PIL.Image")
ImageChops = _LazyModule("PIL.ImageChops")
ImageFilter = _LazyModule("PIL.ImageFilter")
customize = _LazyModule("customize")

try:
    import vdf
except Exception:
    import simple_vdf as vdf

import clips
import ranks
import updater

APP_VERSION = "1.51.0"
APP_ID = f"pg3d-mac-client-v{APP_VERSION}"
STEAM_APP_ID = "2524890"
APP_DIR = "Pixel Gun 3D PC Edition"
APP_BUNDLE = "Pixel Gun 3D.app"
CURSOR_ASSET_NUMBER = 3

BASE = Path(__file__).resolve().parent
WEB = BASE / "web"
CURSOR_CATALOG = json.loads((BASE / "cursor_catalog.json").read_text(encoding="utf-8"))
CURSOR_BY_ID = {x["id"]: x for x in CURSOR_CATALOG}
FPS_SOURCE_DIR = BASE / "fps_unlocker"
FPS_SOURCE_DYLIB = FPS_SOURCE_DIR / "build/PG3DFPSUnlock.dylib"
FPS_SOURCE_LAUNCHER = FPS_SOURCE_DIR / "build/launch-game"
FPS_SOURCE_MOUSEFIX = FPS_SOURCE_DIR / "build/PG3DMouseFix.dylib"

SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
HUD_SELECTOR_FILE = Path.home() / "Library/Application Support/PG3D HUD Asset Selector/hud_selection.json"
RUNTIME = SUPPORT / "runtime"
BACKUPS = SUPPORT / "backups"
ACCOUNT_BACKUPS = SUPPORT / "account_backups"
STATE_DIR = SUPPORT / "state"
GUI_PREVIEWS = RUNTIME / "gui_previews"
CUSTOM_CURSOR = SUPPORT / "custom_cursor.png"
CURSOR_TARGET_JSON = STATE_DIR / "cursor_target.json"
GUI_CATALOG_JSON = STATE_DIR / "gui_catalog.json"
MODS_JSON = STATE_DIR / "mods.json"
HISTORY_JSON = STATE_DIR / "history.json"
PREFS_JSON = STATE_DIR / "prefs.json"
CREDENTIALS_JSON = SUPPORT / "steam_credentials.json"
LOG = SUPPORT / "client.log"
FPS_LOG_DIR = SUPPORT / "fps_logs"
FPS_RUNTIME_DIR = SUPPORT / "fps_runtime" / APP_VERSION
ACCOUNT_HELPER_APP = SUPPORT / "PG3D Account Helper.app"
ACCOUNT_HELPER_HASH = ACCOUNT_HELPER_APP / "Contents/Resources/source.sha256"
ACCOUNT_HELPER_SOURCE = BASE / "native/PG3DAccountHelper.applescript"
ACCOUNT_HELPER_REQUEST = SUPPORT / "helper_request.txt"
ACCOUNT_HELPER_RESULT = SUPPORT / "helper_result.txt"
FPS_DYLIB = FPS_RUNTIME_DIR / "PG3DFPSUnlock.dylib"
FPS_LAUNCHER = FPS_RUNTIME_DIR / "launch-game"
MOUSEFIX_DYLIB = FPS_RUNTIME_DIR / "PG3DMouseFix.dylib"
ORIGINAL_CURSOR_BASELINE = STATE_DIR / "original_cursor_baseline.png"
OVERLAY_CONFIG_JSON = STATE_DIR / "overlay_config.json"
OVERLAY_RUNTIME_JSON = STATE_DIR / "overlay_runtime.json"
OVERLAY_PID_FILE = SUPPORT / f"overlay-v{APP_VERSION}.pid"
OVERLAY_SCRIPT = BASE / "overlay.py"
OVERLAY_EDIT_REQUEST_JSON = STATE_DIR / "overlay_edit_request.json"
OVERLAY_RANK_JSON = STATE_DIR / "overlay_rank.json"
LAST_SESSION_JSON = STATE_DIR / "last_session.json"
_PLAYER_COUNT_CACHE = {"value": None, "at": 0.0}
PLAYTIME_JSON = STATE_DIR / "playtime.json"
WINS_JSON = STATE_DIR / "wins.json"
RANK_STATE_JSON = STATE_DIR / "rank_state.json"
CUSTOMIZE_CATALOG_JSON = STATE_DIR / "customize_catalog.json"
SKY_DIR = SUPPORT / "skies"
SKY_PREVIEWS = RUNTIME / "sky_previews"
GAME_FONTS_DIR = BASE / "fonts_game"
USER_FONTS_DIR = SUPPORT / "fonts"
GAME_FONTS = [  # bundled, all SIL Open Font License
    {"id": "russo", "name": "Russo One", "file": "RussoOne-Regular.ttf"},
    {"id": "bangers", "name": "Bangers", "file": "Bangers-Regular.ttf"},
    {"id": "bebas", "name": "Bebas Neue", "file": "BebasNeue-Regular.ttf"},
    {"id": "pixel", "name": "Press Start", "file": "PressStart2P-Regular.ttf"},
]
KEYCHAIN_SERVICE = "PG3D Mac Client Steam"
# Build the bundled FPS unlocker was written against (see fps_unlocker/src/pg3d_fps_unlock.c).
FPS_SUPPORTED_BUILDS = {"151027"}

for p in (SUPPORT, RUNTIME, BACKUPS, ACCOUNT_BACKUPS, STATE_DIR, GUI_PREVIEWS, FPS_LOG_DIR, FPS_RUNTIME_DIR,
          SKY_DIR, SKY_PREVIEWS):
    p.mkdir(parents=True, exist_ok=True)

_LOCK = threading.RLock()
# Only one asset rebuild at a time; a theme rebuild can take minutes.
_MUTATE_LOCK = threading.Lock()
LAST_REQUEST = time.time()
PENDING_GUARD = None

STATUS = {
    "cursor_phase": "starting",
    "cursor_progress": 0,
    "cursor_message": "Locating cursor asset #3…",
    "cursor_error": None,
    "cursor_target": None,
    "gui_phase": "idle",
    "gui_progress": 0,
    "gui_message": "GUI assets have not been scanned yet.",
    "gui_error": None,
    "launch_phase": "idle",
    "launch_message": "Ready",
    "launch_error": None,
    "last_fps": None,
    "launch_progress": None,
    "launch_eta": None,
    "job_kind": None,
    "job_phase": "idle",
    "job_progress": 0,
    "job_message": "",
    "job_error": None,
}

CURSOR_KEYWORDS = {
    "cursor": 180, "pointer": 160, "mouse": 150, "bubble": 135,
    "crosshair": 115, "reticle": 115, "aim": 90, "touch": 85,
    "tap": 80, "hover": 70, "joystick": 55, "marker": 35,
}
PREFERRED_BUNDLE_WORDS = (
    "ui", "interface", "menu", "hud", "common", "shared", "cursor",
    "input", "battle", "lobby", "gui", "resources"
)
# In-game sprites the Cursor tab can swap with a crosshair design.
SPRITE_ROLES = {
    "hitmarker": re.compile(r"hit.?mark|hit.?indicator|hit.?cross|hit.?confirm|damage.?indicator|hit_?effect", re.I),
    "killicon": re.compile(r"kill.?(icon|mark|feed|notif|confirm|skull)|skull|headshot", re.I),
}
GUI_HINTS = (
    "ui", "hud", "button", "icon", "menu", "panel", "bar", "frame", "window",
    "battle", "health", "armor", "ammo", "weapon", "crosshair", "reticle", "slot",
    "popup", "dialog", "shop", "inventory", "currency", "map", "score", "kill",
    "joystick", "touch", "mobile", "control", "reload", "chat", "clan", "event",
    "pass", "quest", "reward", "settings", "back", "close", "tab", "badge"
)


def log(message: str):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n"
    with _LOCK:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line)


def set_status(**kwargs):
    with _LOCK:
        STATUS.update(kwargs)


_CACHE = {}
_CACHE_LOCK = threading.Lock()


def cached(key, ttl, fn):
    """Return fn() but reuse the last result for `ttl` seconds.

    The UI polls /api/status every couple of seconds; without this every poll
    spawned several pgrep/PlistBuddy processes and re-parsed Steam's VDF files.
    """
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


def invalidate(*keys):
    with _CACHE_LOCK:
        if not keys:
            _CACHE.clear()
        for k in keys:
            _CACHE.pop(k, None)


def read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"Could not read {path}: {e}")
    return copy.deepcopy(default)


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def path_key(path: Path):
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:20]


def alpha_bbox(img: Image.Image):
    rgba = img.convert("RGBA")
    bbox = rgba.getchannel("A").getbbox()
    if not bbox:
        return (0, 0, rgba.width, rgba.height)
    return tuple(map(int, bbox))


def trim_alpha(img: Image.Image):
    rgba = img.convert("RGBA")
    bbox = rgba.getchannel("A").getbbox()
    return rgba.crop(bbox) if bbox else rgba


def prepare_fps_runtime():
    """Copy the bundled FPS helper to a stable Application Support location."""
    try:
        FPS_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        for src, dst in ((FPS_SOURCE_DYLIB, FPS_DYLIB), (FPS_SOURCE_LAUNCHER, FPS_LAUNCHER),
                         (FPS_SOURCE_MOUSEFIX, MOUSEFIX_DYLIB)):
            if src.is_file() and src.stat().st_size > 0:
                stamp = dst.with_name(dst.name + ".src-sha256")
                src_sha = sha256_file(src)
                if not dst.exists() or (stamp.read_text().strip() if stamp.exists() else sha256_file(dst)) != src_sha:
                    shutil.copy2(src, dst)
                    if dst in (MOUSEFIX_DYLIB, FPS_DYLIB):
                        # Built without Apple's tools, so they get their ad-hoc signature here.
                        try:
                            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(dst)],
                                           capture_output=True, timeout=30)
                        except Exception as e:
                            log(f"Signing the mouse fix failed: {e}")
                    stamp.write_text(src_sha)
            if dst.exists():
                dst.chmod(dst.stat().st_mode | 0o111)
        return FPS_DYLIB.is_file() and FPS_DYLIB.stat().st_size > 0 and FPS_LAUNCHER.is_file() and FPS_LAUNCHER.stat().st_size > 0
    except Exception as e:
        log(f"FPS runtime preparation failed: {e}")
        return False


def fps_bundle_present():
    if FPS_DYLIB.is_file() and FPS_DYLIB.stat().st_size > 0 and FPS_LAUNCHER.is_file() and FPS_LAUNCHER.stat().st_size > 0:
        return True
    return prepare_fps_runtime()


def steam_root():
    return Path.home() / "Library/Application Support/Steam"


def parse_libraries(vdf_path: Path):
    out = []
    if not vdf_path.exists():
        return out
    try:
        txt = vdf_path.read_text(errors="ignore")
        for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
            out.append(Path(m.group(1).replace("\\\\", "\\")).expanduser())
    except Exception as e:
        log(f"libraryfolders parse failed: {e}")
    return out


def find_app():
    return cached("find_app", 30, _find_app_uncached)


def _find_app_uncached():
    steam = steam_root()
    libs = [steam] + parse_libraries(steam / "steamapps/libraryfolders.vdf")
    seen = set()
    for lib in libs:
        key = str(lib)
        if key in seen:
            continue
        seen.add(key)
        for p in (
            lib / "steamapps/common" / APP_DIR / APP_BUNDLE,
            lib / "steamapps/common" / APP_BUNDLE,
        ):
            if p.exists():
                return p
    common = steam / "steamapps/common"
    if common.exists():
        for p in common.glob("*/Pixel Gun 3D.app"):
            if p.exists():
                return p
    return None


def plist_value(plist: Path, key: str, default="unknown"):
    try:
        mtime = plist.stat().st_mtime
    except OSError:
        return default
    return cached(("plist", str(plist), key, mtime), 3600, lambda: _plist_value_uncached(plist, key, default))


def _plist_value_uncached(plist: Path, key: str, default="unknown"):
    try:
        with open(plist, "rb") as f:
            val = plistlib.load(f).get(key)
        if val not in (None, ""):
            return str(val)
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", f"Print :{key}", str(plist)],
            capture_output=True, text=True, timeout=4
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return default


def game_running():
    return cached("game_running", 1.5, _game_running_uncached)


def _game_running_uncached():
    # Match the game's own executable, not any command line that merely mentions
    # its path (helpers inspecting the binary made the game look "running" for a
    # moment, which started and stopped the replay buffer).
    try:
        r = subprocess.run(["/bin/ps", "-axo", "comm="], capture_output=True, text=True, timeout=5)
        return any(c.strip().endswith("/Contents/MacOS/Pixel Gun 3D") or c.strip() == "Pixel Gun 3D"
                   for c in r.stdout.splitlines())
    except Exception:
        return False


def pg3d_pids():
    try:
        r = subprocess.run(["/bin/ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=5)
    except Exception:
        return []
    out = []
    for line in r.stdout.splitlines():
        pid, _, comm = line.strip().partition(" ")
        comm = comm.strip()
        if pid.isdigit() and (comm.endswith("/Contents/MacOS/Pixel Gun 3D") or comm == "Pixel Gun 3D"):
            out.append(int(pid))
    return out


def pg3d_window_count():
    """PG3D's windows (any space), or None when macOS can't tell us."""
    Q = getattr(clips, "Quartz", None)
    if Q is None:
        return None
    try:
        wins = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionAll, Q.kCGNullWindowID) or []
        return sum(1 for w in wins if "pixel gun" in str(w.get("kCGWindowOwnerName") or "").lower()
                   and int(w.get("kCGWindowLayer", 1)) == 0)
    except Exception:
        return None


def game_stuck():
    """PG3D has been running a while without any window: hung in the background."""
    start = STATUS.get("session_start")
    if not start or time.time() - start < 45 or not game_running():
        return False
    return pg3d_window_count() == 0


def quit_game(force=False):
    pids = pg3d_pids()
    if not pids:
        return {"ok": True, "quit": False}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + 5
    while time.time() < deadline and pg3d_pids():
        time.sleep(0.25)
    for pid in pg3d_pids():  # didn't react: it's hung
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    invalidate("game_running")
    log(f"Quit PG3D (pids {pids})")
    return {"ok": True, "quit": True}


def steam_running():
    return cached("steam_running", 2.0, _steam_running_uncached)


def _steam_running_uncached():
    try:
        for args in (["pgrep", "-x", "steam_osx"], ["pgrep", "-f", "Steam.app/Contents/MacOS"]):
            if subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return True
    except Exception:
        pass
    return False


def candidate_files(root: Path, deep=False):
    out = []
    if not root.exists():
        return out
    for p in root.iterdir():
        if not p.is_file():
            continue
        if (p.name in ("globalgamemanagers", "resources.assets") or
                p.suffix.lower() in (".assets", ".bundle") or
                p.name.startswith(("sharedassets", "level"))):
            out.append(p)
    sa = root / "StreamingAssets"
    if sa.exists():
        for p in sa.rglob("*"):
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size < 4096 or size > 350 * 1024 * 1024:
                continue
            rel = str(p.relative_to(sa)).lower()
            if not deep and not any(k in rel for k in PREFERRED_BUNDLE_WORDS):
                continue
            if p.suffix.lower() in (
                ".json", ".txt", ".xml", ".png", ".jpg", ".jpeg", ".webp",
                ".mp3", ".wav", ".ogg", ".mp4", ".mov", ".plist", ".dylib",
                ".dll", ".so", ".ress", ".resource", ".resources"
            ):
                continue
            out.append(p)
    uniq, seen = [], set()
    for p in out:
        s = str(p)
        if s not in seen:
            seen.add(s)
            uniq.append(p)
    return uniq


def obj_name(obj):
    try:
        return obj.peek_name() or ""
    except Exception:
        try:
            return getattr(obj.parse_as_object(), "m_Name", "") or ""
        except Exception:
            return ""


def obj_dims(data):
    w = int(getattr(data, "m_Width", 0) or 0)
    h = int(getattr(data, "m_Height", 0) or 0)
    if not w or not h:
        try:
            return tuple(map(int, data.image.size))
        except Exception:
            return 0, 0
    return w, h


def cursor_score(name, w, h, typ):
    n = (name or "").lower()
    score = sum(v for k, v in CURSOR_KEYWORDS.items() if k in n)
    if 4 <= w <= 256 and 4 <= h <= 256:
        score += 25
    if 8 <= w <= 128 and 8 <= h <= 128:
        score += 15
    if max(w, h) and abs(w - h) / max(w, h) < 0.18:
        score += 5
    if typ == "Sprite":
        score += 8
    return score


def scan_cursor_target(force=False):
    if not force and CURSOR_TARGET_JSON.exists():
        try:
            t = json.loads(CURSOR_TARGET_JSON.read_text(encoding="utf-8"))
            if int(t.get("asset_number", 0)) == CURSOR_ASSET_NUMBER and Path(t["source_file"]).exists():
                export_cursor_original(t)
                set_status(cursor_phase="ready", cursor_progress=100,
                           cursor_message="Cursor asset #3 locked.", cursor_error=None,
                           cursor_target=t)
                return t
        except Exception:
            pass

    app = find_app()
    if not app:
        raise RuntimeError("Pixel Gun 3D.app was not found in your Steam libraries.")
    root = app / "Contents/Resources/Data"
    files = candidate_files(root, deep=False)
    if not files:
        raise RuntimeError("No readable Unity asset files were found in the PG3D app.")

    found = []
    total = max(1, len(files))
    set_status(cursor_phase="scanning", cursor_progress=1,
               cursor_message="Locating cursor asset #3…", cursor_error=None)
    for idx, p in enumerate(files, 1):
        set_status(cursor_progress=max(1, int(idx / total * 92)),
                   cursor_message=f"Scanning cursor assets ({idx}/{total})…")
        try:
            env = UnityPy.load(str(p))
        except Exception as e:
            log(f"CURSOR LOAD FAIL {p}: {type(e).__name__}: {e}")
            continue
        for obj in env.objects:
            if obj.type.name not in ("Texture2D", "Sprite"):
                continue
            try:
                data = obj.parse_as_object()
                name = obj_name(obj)
                w, h = obj_dims(data)
                s = cursor_score(name, w, h, obj.type.name)
                if s < 20 or not (1 <= w <= 512 and 1 <= h <= 512):
                    continue
                found.append({
                    "source_file": str(p),
                    "relative_file": str(p.relative_to(app)),
                    "object_type": obj.type.name,
                    "path_id": int(obj.path_id),
                    "name": name,
                    "width": int(w),
                    "height": int(h),
                    "score": int(s),
                })
            except Exception as e:
                log(f"CURSOR OBJ FAIL {p} {getattr(obj, 'path_id', '?')}: {e}")

    found.sort(key=lambda c: (-c["score"], c["width"] * c["height"], c["name"].lower()))
    if len(found) < CURSOR_ASSET_NUMBER:
        raise RuntimeError(f"Only {len(found)} cursor candidates were found; asset #3 does not exist.")
    target = dict(found[CURSOR_ASSET_NUMBER - 1])
    target.update({
        "asset_number": CURSOR_ASSET_NUMBER,
        "app": str(app),
        "detected_at": datetime.now().isoformat(timespec="seconds"),
    })
    write_json(CURSOR_TARGET_JSON, target)
    export_cursor_original(target)
    set_status(cursor_phase="ready", cursor_progress=100,
               cursor_message="Cursor asset #3 locked.", cursor_error=None,
               cursor_target=target)
    log(f"Cursor target #3: {target['name']} {target['object_type']} {target['width']}x{target['height']} path={target['path_id']}")
    return target


def load_cursor_target():
    try:
        if CURSOR_TARGET_JSON.exists():
            t = json.loads(CURSOR_TARGET_JSON.read_text(encoding="utf-8"))
            if Path(t["source_file"]).exists():
                return t
    except Exception:
        pass
    return None


def find_reader(env, path_id: int):
    for obj in env.objects:
        if int(obj.path_id) == int(path_id):
            return obj
    return None


def export_cursor_original(target):
    """Keep one immutable visual baseline for cursor sizing and previews."""
    try:
        runtime_original = RUNTIME / "original_cursor.png"
        # If an old build says a cursor mod is active, its stored original preview
        # is safer than reading the currently modified game asset. Otherwise read
        # the live target so 100% calibrates to the actual stock PG3D cursor.
        old_cursor_mod = bool(read_json(MODS_JSON, {}).get("cursor"))
        if not ORIGINAL_CURSOR_BASELINE.exists() and old_cursor_mod and runtime_original.exists():
            try:
                Image.open(runtime_original).convert("RGBA").save(ORIGINAL_CURSOR_BASELINE, "PNG")
            except Exception:
                pass

        if not ORIGINAL_CURSOR_BASELINE.exists():
            env = UnityPy.load(target["source_file"])
            reader = find_reader(env, target["path_id"])
            if not reader:
                raise RuntimeError("Cursor object could not be read.")
            img = reader.parse_as_object().image.convert("RGBA")
            img.save(ORIGINAL_CURSOR_BASELINE, "PNG")
        shutil.copy2(ORIGINAL_CURSOR_BASELINE, runtime_original)

        img = Image.open(ORIGINAL_CURSOR_BASELINE).convert("RGBA")
        x0, y0, x1, y1 = alpha_bbox(img)
        target["visible_bbox"] = [x0, y0, x1, y1]
        target["visible_width"] = max(1, x1 - x0)
        target["visible_height"] = max(1, y1 - y0)
        write_json(CURSOR_TARGET_JSON, target)
    except Exception as e:
        log(f"Cursor original preview failed: {e}")


def gui_catalog():
    return read_json(GUI_CATALOG_JSON, {"generated": None, "app": None, "items": []})


def sprite_pivot(data, w, h):
    try:
        p = data.m_Pivot
        x = float(p.x)
        y = float(p.y)
        # Unity sprite pivots are normally normalized here. Convert pixel values if needed.
        if abs(x) > 1.5 or abs(y) > 1.5:
            x = x / max(1.0, float(w))
            y = y / max(1.0, float(h))
        return max(-2.0, min(3.0, x)), max(-2.0, min(3.0, y))
    except Exception:
        return 0.5, 0.5


def gui_score(name: str, rel: str, w: int, h: int):
    n = (name or "").lower()
    r = rel.lower()
    s = 0
    for hint in GUI_HINTS:
        if hint in n:
            s += 16
        if hint in r:
            s += 4
    if name:
        s += 8
    if 8 <= w <= 512 and 8 <= h <= 512:
        s += 10
    if w <= 256 and h <= 256:
        s += 4
    return s


def scan_gui_assets(deep=False):
    app = find_app()
    if not app:
        raise RuntimeError("Pixel Gun 3D.app was not found.")
    root = app / "Contents/Resources/Data"
    files = candidate_files(root, deep=deep)
    if not files:
        raise RuntimeError("No readable Unity asset files were found.")

    GUI_PREVIEWS.mkdir(parents=True, exist_ok=True)
    for p in GUI_PREVIEWS.glob("*.png"):
        p.unlink(missing_ok=True)

    items = []
    total = max(1, len(files))
    set_status(gui_phase="scanning", gui_progress=1, gui_error=None,
               gui_message="Scanning PG3D sprite assets…")

    for idx, p in enumerate(files, 1):
        set_status(gui_progress=max(1, int(idx / total * 95)),
                   gui_message=f"Scanning GUI sprites ({idx}/{total})…")
        rel = str(p.relative_to(app))
        try:
            env = UnityPy.load(str(p))
        except Exception as e:
            log(f"GUI LOAD FAIL {p}: {type(e).__name__}: {e}")
            continue
        for obj in env.objects:
            if obj.type.name != "Sprite":
                continue
            try:
                data = obj.parse_as_object()
                img = data.image.convert("RGBA")
                w, h = img.size
                if not (6 <= w <= 1024 and 6 <= h <= 1024):
                    continue
                name = obj_name(obj)
                s = gui_score(name, rel, w, h)
                role = next((r for r, rx in SPRITE_ROLES.items() if rx.search(name or "")), None)
                if role:
                    s += 200  # always keep these in the catalog
                # Normal scan filters obvious low-value art. Deep scan keeps every reasonable sprite.
                if not deep and s < 12:
                    continue
                px, py = sprite_pivot(data, w, h)
                items.append({
                    "source_file": str(p),
                    "relative_file": rel,
                    "path_id": int(obj.path_id),
                    "name": name,
                    "width": int(w),
                    "height": int(h),
                    "pivot_x": px,
                    "pivot_y": py,
                    "score": int(s),
                    "role": role,
                    "_image": img,
                })
            except Exception as e:
                log(f"GUI OBJ FAIL {p} {getattr(obj, 'path_id', '?')}: {e}")

    items.sort(key=lambda x: (-x["score"], x["name"].lower(), x["relative_file"], x["path_id"]))
    # Prevent accidental browser overload while retaining a very large asset browser.
    max_items = 1400 if deep else 800
    items = items[:max_items]
    output = []
    for idx, item in enumerate(items, 1):
        img = item.pop("_image")
        preview_name = f"{idx:04d}.png"
        try:
            # Preserve transparent frame dimensions, but cap huge thumbnails.
            if max(img.size) > 512:
                tmp = img.copy()
                tmp.thumbnail((512, 512), Image.Resampling.LANCZOS)
                tmp.save(GUI_PREVIEWS / preview_name)
            else:
                img.save(GUI_PREVIEWS / preview_name)
        except Exception as e:
            log(f"GUI preview export failed #{idx}: {e}")
            continue
        item.update({
            "id": idx,
            "key": f"{path_key(Path(item['source_file']))}:{item['path_id']}",
            "preview": f"/api/gui-preview/{preview_name}",
        })
        output.append(item)

    data = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "app": str(app),
        "deep": bool(deep),
        "items": output,
    }
    write_json(GUI_CATALOG_JSON, data)
    set_status(gui_phase="ready", gui_progress=100, gui_error=None,
               gui_message=f"Found {len(output)} GUI sprite candidates.")
    log(f"GUI scan complete: {len(output)} items (deep={deep})")
    return data


def start_gui_scan(deep=False):
    with _LOCK:
        if STATUS.get("gui_phase") == "scanning":
            return
        set_status(gui_phase="scanning", gui_progress=0, gui_error=None,
                   gui_message="Starting GUI asset scan…")

    def work():
        try:
            scan_gui_assets(deep=deep)
        except Exception as e:
            log(traceback.format_exc())
            set_status(gui_phase="error", gui_progress=0, gui_error=str(e),
                       gui_message="GUI scan failed.")
    threading.Thread(target=work, daemon=True).start()


def default_mods():
    return {"cursor": None, "gui": {}, "hud_set": {}, "hud_disabled": False, "theme": None, "skybox": None,
            "loading": None, "font": None}


def load_mods():
    m = read_json(MODS_JSON, default_mods())
    for k, v in default_mods().items():
        if k not in m:
            m[k] = copy.deepcopy(v)
    return m


def save_mods(mods):
    write_json(MODS_JSON, mods)


def default_history():
    return {"undo": [], "redo": []}


def push_history(before, after, label):
    hist = read_json(HISTORY_JSON, default_history())
    hist["undo"].append({"state": before, "label": label, "at": datetime.now().isoformat(timespec="seconds")})
    hist["undo"] = hist["undo"][-25:]
    hist["redo"] = []
    write_json(HISTORY_JSON, hist)
    save_mods(after)


def sources_for_state(state):
    out = set()
    c = state.get("cursor")
    if c and c.get("target", {}).get("source_file"):
        out.add(c["target"]["source_file"])
    for op in state.get("gui", {}).values():
        if op.get("source_file"):
            out.add(op["source_file"])
    if state.get("hud_disabled"):
        for op in state.get("hud_set", {}).values():
            if op.get("source_file"):
                out.add(op["source_file"])
    theme = state.get("theme")
    if theme:
        out.update(theme.get("targets", {}).keys())
    for key in ("skybox", "loading", "font"):
        sky = state.get(key)
        if sky:
            out.update(t["source_file"] for t in sky.get("targets", []) if t.get("source_file"))
    return out


def manifest_folder(source: Path):
    return BACKUPS / path_key(source)


def load_manifest(source: Path):
    return read_json(manifest_folder(source) / "manifest.json", None)


def write_manifest(source: Path, manifest):
    write_json(manifest_folder(source) / "manifest.json", manifest)


def archive_manifest(source: Path):
    folder = manifest_folder(source)
    if folder.exists():
        archive = folder.with_name(folder.name + "_archived_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.move(str(folder), str(archive))
        log(f"Archived stale asset backup to {archive}")


def ensure_baseline(source: Path):
    if not source.exists():
        raise RuntimeError(f"Asset file is missing: {source}")
    current = sha256_file(source)
    m = load_manifest(source)
    if not m:
        folder = manifest_folder(source)
        folder.mkdir(parents=True, exist_ok=True)
        original = folder / "original_unmodified_asset"
        shutil.copy2(source, original)
        m = {
            "source": str(source),
            "original": str(original),
            "original_sha256": current,
            "last_mod_sha256": None,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        write_manifest(source, m)
        return m
    allowed = {m.get("original_sha256"), m.get("last_mod_sha256")}
    allowed.discard(None)
    if current not in allowed or not Path(m.get("original", "")).exists():
        archive_manifest(source)
        # Current file is treated as the new Steam/game-update baseline.
        folder = manifest_folder(source)
        folder.mkdir(parents=True, exist_ok=True)
        original = folder / "original_unmodified_asset"
        shutil.copy2(source, original)
        m = {
            "source": str(source),
            "original": str(original),
            "original_sha256": current,
            "last_mod_sha256": None,
            "created": datetime.now().isoformat(timespec="seconds"),
            "reason": "external asset change / game update",
        }
        write_manifest(source, m)
        log(f"Established new clean baseline for changed game asset: {source}")
    return m


def save_parsed_object(data, reader):
    """Persist parsed Unity objects using UnityPy's supported high-level save path."""
    save = getattr(data, "save", None)
    if callable(save):
        save()
        return
    # Compatibility fallback for older/newer UnityPy object wrappers.
    reader.patch(data)


def save_env(env, source: Path):
    tmp = source.with_name(source.name + ".pg3d_client_tmp")
    raw = env.file.save()
    tmp.write_bytes(raw)
    if tmp.stat().st_size < 128:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Unity output was unexpectedly empty.")
    # Validate the serialized container before replacing the live game file.
    # This specifically prevents the malformed-buffer failure seen in v1.2.2
    # from ever leaving a damaged asset in the PG3D installation.
    try:
        check = UnityPy.load(str(tmp))
        _ = check.objects
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Unity asset write failed validation: {e}")
    os.replace(tmp, source)


def image_diff(a, b):
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    d = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
    return sum(v * (i % 256) for i, v in enumerate(d.histogram()))


def sprite_render_data(spr):
    """Texture + rect a Sprite draws from: its own render data, or its SpriteAtlas entry."""
    atlas = None
    try:
        if spr.m_SpriteAtlas:
            atlas = spr.m_SpriteAtlas.deref_parse_as_object()
        elif getattr(spr, "m_AtlasTags", None):
            for obj in spr.assets_file.objects.values():
                if obj.type.name == "SpriteAtlas" and obj.peek_name() == spr.m_AtlasTags[0]:
                    atlas = obj.parse_as_object()
                    break
    except Exception:
        atlas = None
    if atlas is not None:
        for key, value in atlas.m_RenderDataMap:
            if key == spr.m_RenderDataKey:
                return value
    return spr.m_RD


def sprite_texture(spr):
    rd = sprite_render_data(spr)
    try:
        return rd.texture.deref(), rd.textureRect
    except Exception:
        return None, rd.textureRect


def sprite_xy(atlas, sprite, rect):
    x, y, w, h = [int(round(float(v))) for v in rect]
    options = []
    for xx, yy in ((x, atlas.height - y - h), (x, y)):
        if xx >= 0 and yy >= 0 and xx + w <= atlas.width and yy + h <= atlas.height:
            options.append((image_diff(atlas.crop((xx, yy, xx + w, yy + h)), sprite), xx, yy, w, h))
    if not options:
        raise RuntimeError("A sprite rectangle is outside its texture atlas.")
    options.sort(key=lambda z: z[0])
    return options[0][1:]


def _hex_rgb(value, default=(255, 255, 255)):
    try:
        h = str(value).lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except Exception:
        return default


def recolor_cursor(icon: Image.Image, color="#FFFFFF", outline=None, outline_width=0):
    """Tint a white cursor sprite and optionally add a contrasting outline.

    The design PNGs are white shapes (some already carry a faint built-in dark
    edge). We replace the fill with `color`; if `outline` is given we grow the
    shape's alpha and lay the fill over an outline-coloured halo so it stays
    readable on any background in game.
    """
    icon = icon.convert("RGBA")
    r, g, b, a = icon.split()
    cr, cg, cb = _hex_rgb(color)
    fill = Image.merge("RGBA", (
        Image.new("L", icon.size, cr),
        Image.new("L", icon.size, cg),
        Image.new("L", icon.size, cb),
        a,
    ))
    try:
        ow = int(round(float(outline_width)))
    except Exception:
        ow = 0
    if not outline or ow <= 0:
        return fill
    orr, org, orb = _hex_rgb(outline, (0, 0, 0))
    # Grow alpha by ow px (scaled to this sprite) for the halo.
    grow = max(1, ow)
    grown = a.filter(ImageFilter.MaxFilter(grow * 2 + 1))
    halo = Image.merge("RGBA", (
        Image.new("L", icon.size, orr),
        Image.new("L", icon.size, org),
        Image.new("L", icon.size, orb),
        grown,
    ))
    out = Image.alpha_composite(halo, fill)
    return out


def fit_cursor(icon: Image.Image, size, percent, base_bbox=None):
    """Render a replacement relative to the original cursor's visible pixels.

    100% now means the same visible bounding box as the stock PG3D cursor, not
    the entire transparent texture frame. This is what the game visually scales.
    """
    w, h = map(int, size)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im = icon.convert("RGBA").copy()
    if base_bbox and len(base_bbox) == 4:
        x0, y0, x1, y1 = [int(round(float(v))) for v in base_bbox]
        bw = max(1, x1 - x0)
        bh = max(1, y1 - y0)
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
    else:
        bw, bh = w, h
        cx, cy = w / 2.0, h / 2.0
    scale = max(0.01, float(percent) / 100.0)
    tw = max(1, int(round(bw * scale)))
    th = max(1, int(round(bh * scale)))
    im.thumbnail((tw, th), Image.Resampling.LANCZOS)
    x = int(round(cx - im.width / 2.0))
    y = int(round(cy - im.height / 2.0))
    out.alpha_composite(im, (x, y))
    return out


def scale_sprite_about_pivot(img: Image.Image, scale_percent: float, pivot_x: float, pivot_y: float):
    """Scale visual pixels inside the existing frame while pinning Unity's original pivot."""
    w, h = img.size
    scale = max(0.05, min(1.0, float(scale_percent) / 100.0))
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    # PIL's Y axis is top-down. Unity pivot Y is bottom-up.
    fixed_px = pivot_x * w
    fixed_py = (1.0 - pivot_y) * h
    new_px = pivot_x * nw
    new_py = (1.0 - pivot_y) * nh
    x = int(round(fixed_px - new_px))
    y = int(round(fixed_py - new_py))
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.alpha_composite(resized, (x, y))
    return out


def cursor_image_path(cursor_id: str):
    if cursor_id == "custom":
        if not CUSTOM_CURSOR.exists():
            raise RuntimeError("No custom cursor has been imported yet.")
        return CUSTOM_CURSOR
    item = CURSOR_BY_ID.get(cursor_id)
    if not item:
        raise RuntimeError("Unknown cursor design.")
    return WEB / item["file"]


def _cursor_style(color=None, outline=None, outline_width=0):
    color = color if isinstance(color, str) and color else "#FFFFFF"
    outline = outline if isinstance(outline, str) and outline else None
    try:
        ow = max(0, min(6, int(round(float(outline_width)))))
    except Exception:
        ow = 0
    return color, outline, ow


def styled_cursor_icon(cursor_id, color=None, outline=None, outline_width=0):
    icon = Image.open(cursor_image_path(cursor_id)).convert("RGBA")
    color, outline, ow = _cursor_style(color, outline, outline_width)
    if color.upper() == "#FFFFFF" and not (outline and ow):
        return icon
    return recolor_cursor(icon, color=color, outline=outline, outline_width=ow)


def render_cursor_preview(cursor_id: str, size_percent, color=None, outline=None, outline_width=0):
    """Render the exact RGBA frame written by the cursor patcher."""
    target = load_cursor_target() or scan_cursor_target()
    try:
        pct = max(8.0, min(100.0, float(size_percent)))
    except Exception:
        pct = 45.0
    icon = styled_cursor_icon(cursor_id, color, outline, outline_width)
    frame = fit_cursor(icon, (int(target["width"]), int(target["height"])), pct, target.get("visible_bbox"))
    bio = io.BytesIO()
    frame.save(bio, format="PNG", optimize=True)
    return bio.getvalue(), int(target["width"]), int(target["height"])


def all_operations_for_source(state, source_str: str):
    gui = []
    for op in state.get("gui", {}).values():
        if op.get("source_file") == source_str:
            gui.append(op)
    if state.get("hud_disabled"):
        for key, meta in state.get("hud_set", {}).items():
            if meta.get("source_file") == source_str:
                # Do not duplicate an explicit GUI op; hide wins below anyway.
                gui.append({**meta, "key": key, "hidden": True, "scale": meta.get("scale", 100), "_hud": True})
    return gui


def _load_sky_image(sky):
    path = SKY_DIR / Path(str(sky.get("image", ""))).name
    if not path.is_file():
        raise RuntimeError("The selected sky image is missing. Pick or upload it again.")
    return Image.open(path).convert("RGBA")


def rebuild_sources(source_strings, state=None, progress=None):
    if game_running():
        raise RuntimeError("Close Pixel Gun 3D before applying or resetting asset changes.")
    state = state or load_mods()
    theme = state.get("theme") or None
    sky = state.get("skybox") or None
    sky_image = None
    ordered = sorted(set(source_strings))
    for index, source_str in enumerate(ordered, 1):
        if progress:
            progress((index - 1) / max(1, len(ordered)), f"Rebuilding game files ({index}/{len(ordered)})…")
        source = Path(source_str)
        if not source.exists():
            log(f"Skipping missing asset during rebuild: {source}")
            continue
        manifest = ensure_baseline(source)
        original = Path(manifest["original"])
        needs_cursor = bool(state.get("cursor") and state["cursor"].get("target", {}).get("source_file") == source_str)
        gui_ops = all_operations_for_source(state, source_str)
        sky_ops = [t for t in (sky or {}).get("targets", []) if t.get("source_file") == source_str]
        sky_pids = {int(t["path_id"]) for t in sky_ops}
        load_cfg = state.get("loading") or None
        load_ops = [t for t in (load_cfg or {}).get("targets", []) if t.get("source_file") == source_str]
        sky_pids |= {int(t["path_id"]) for t in load_ops}
        theme_pids = {int(x) for x in (theme or {}).get("targets", {}).get(source_str, [])} - sky_pids
        if needs_cursor and state["cursor"]["target"].get("object_type") == "Texture2D":
            theme_pids.discard(int(state["cursor"]["target"]["path_id"]))
        if sky_ops and sky_image is None:
            sky_image = _load_sky_image(sky)
        font_ops = [t for t in ((state.get("font") or {}).get("targets") or []) if t.get("source_file") == source_str]
        if not needs_cursor and not gui_ops and not theme_pids and not sky_ops and not load_ops and not font_ops:
            shutil.copy2(original, source)
            manifest["last_mod_sha256"] = None
            manifest["restored"] = datetime.now().isoformat(timespec="seconds")
            write_manifest(source, manifest)
            continue

        shutil.copy2(original, source)
        try:
            env = UnityPy.load(str(source))
            atlas_cache = {}  # texture path id -> {reader,data,image,orig,dirty}

            def themed(img):
                return customize.recolor_image(img, theme["preset"], theme.get("strength", 0.85))

            def atlas_entry(tex_reader):
                pid = int(tex_reader.path_id)
                if pid not in atlas_cache:
                    data = tex_reader.parse_as_object()
                    image = data.image.convert("RGBA")
                    entry = {"reader": tex_reader, "data": data, "image": image, "orig": image, "dirty": False}
                    if pid in theme_pids:
                        # Recolour the whole atlas first; cursor/GUI edits are
                        # composited on top. "orig" keeps sprite lookup exact.
                        entry["orig"] = image.copy()
                        entry["image"] = themed(image)
                        entry["dirty"] = True
                    atlas_cache[pid] = entry
                return atlas_cache[pid]

            # Cursor operation first.
            if needs_cursor:
                c = state["cursor"]
                target = c["target"]
                reader = find_reader(env, int(target["path_id"]))
                if reader is None:
                    raise RuntimeError("Cursor asset #3 moved. Use Re-detect Cursor Asset.")
                icon = styled_cursor_icon(c["cursor"], c.get("color"), c.get("outline"), c.get("outline_width", 0))
                pct = float(c["size"])
                if target["object_type"] == "Texture2D":
                    tex = reader.parse_as_object()
                    old = tex.image.convert("RGBA")
                    tex.image = fit_cursor(icon, old.size, pct, target.get("visible_bbox"))
                    save_parsed_object(tex, reader)
                elif target["object_type"] == "Sprite":
                    spr = reader.parse_as_object()
                    old_sprite = spr.image.convert("RGBA")
                    tr, r = sprite_texture(spr)
                    if tr is None:
                        raise RuntimeError("Cursor sprite texture atlas could not be resolved.")
                    entry = atlas_entry(tr)
                    x, y, w, h = sprite_xy(entry["orig"], old_sprite, [r.x, r.y, r.width, r.height])
                    entry["image"].paste((0, 0, 0, 0), (x, y, x + w, y + h))
                    entry["image"].alpha_composite(fit_cursor(icon, (w, h), pct, target.get("visible_bbox")), (x, y))
                    entry["dirty"] = True
                else:
                    raise RuntimeError("Unsupported cursor asset type.")

            # GUI sprite operations. Last operation for a path ID wins.
            merged = {}
            for op in gui_ops:
                merged[int(op["path_id"])] = op
            for path_id, op in merged.items():
                reader = find_reader(env, path_id)
                if reader is None or reader.type.name != "Sprite":
                    log(f"GUI asset path {path_id} moved or is not a Sprite; skipped")
                    continue
                spr = reader.parse_as_object()
                old_sprite = spr.image.convert("RGBA")
                tr, r = sprite_texture(spr)
                if tr is None:
                    log(f"GUI asset path {path_id} has unresolved texture; skipped")
                    continue
                entry = atlas_entry(tr)
                x, y, w, h = sprite_xy(entry["orig"], old_sprite, [r.x, r.y, r.width, r.height])
                entry["image"].paste((0, 0, 0, 0), (x, y, x + w, y + h))
                hidden = bool(op.get("hidden", False))
                rep = op.get("replace")
                if rep and not hidden:
                    try:
                        if rep.get("file"):
                            icon = Image.open(ROLE_DIR / op.get("role", "") / Path(rep["file"]).name).convert("RGBA")
                        else:
                            icon = styled_cursor_icon(rep["cursor"], rep.get("color"), rep.get("outline"), rep.get("outline_width", 0))
                        entry["image"].alpha_composite(fit_cursor(icon, (w, h), float(rep.get("size", 90))), (x, y))
                    except Exception as e:
                        log(f"Sprite swap skipped for {path_id}: {e}")
                        entry["image"].alpha_composite(old_sprite.resize((w, h)) if old_sprite.size != (w, h) else old_sprite, (x, y))
                elif not hidden:
                    scale = max(10.0, min(100.0, float(op.get("scale", 100))))
                    px = float(op.get("pivot_x", 0.5))
                    py = float(op.get("pivot_y", 0.5))
                    if int(tr.path_id) in theme_pids:
                        old_sprite = themed(old_sprite)
                    changed = scale_sprite_about_pivot(old_sprite, scale, px, py)
                    if changed.size != (w, h):
                        changed = changed.resize((w, h), Image.Resampling.LANCZOS)
                    entry["image"].alpha_composite(changed, (x, y))
                entry["dirty"] = True

            # Theme: every remaining GUI texture in this file.
            if theme_pids:
                readers = {int(o.path_id): o for o in env.objects}
                for pid in sorted(theme_pids):
                    reader = readers.get(pid)
                    if reader is None or reader.type.name != "Texture2D":
                        continue
                    try:
                        atlas_entry(reader)
                    except Exception as e:
                        log(f"Theme skipped texture {pid} in {source.name}: {e}")

            # Skybox textures.
            skipped = {}
            for t in sky_ops:
                reader = find_reader(env, int(t["path_id"]))
                if reader is None:
                    skipped["sky textures"] = skipped.get("sky textures", 0) + 1
                    continue
                data = reader.parse_as_object()
                if reader.type.name == "Cubemap":
                    size = int(data.m_Width) or int(t.get("width", 512))
                    faces = customize.cubemap_faces(sky_image, size, sky.get("yaw", 0), sky.get("swap_lr", False))
                    customize.write_cubemap(data, faces, flip=not sky.get("flip", False))
                else:
                    img = customize.sky_image_for({**t, "width": data.m_Width, "height": data.m_Height},
                                                  sky_image, sky.get("yaw", 0), sky.get("swap_lr", False))
                    if sky.get("flip"):
                        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                    data.image = img
                save_parsed_object(data, reader)

            # Loading screens: the picked image, filling the texture like CSS cover.
            if load_ops:
                load_img = _load_sky_image(load_cfg)
                for t in load_ops:
                    reader = find_reader(env, int(t["path_id"]))
                    if reader is None or reader.type.name != "Texture2D":
                        skipped["loading screens"] = skipped.get("loading screens", 0) + 1
                        continue
                    data = reader.parse_as_object()
                    data.image = customize.cover(load_img, (int(data.m_Width), int(data.m_Height)))
                    save_parsed_object(data, reader)

            # Font swap.
            for t in font_ops:
                reader = find_reader(env, int(t["path_id"]))
                if reader is None or reader.type.name != "Font":
                    skipped["fonts"] = skipped.get("fonts", 0) + 1
                    continue
                data = reader.parse_as_object()
                customize.replace_font_data(data, font_bytes(state["font"]))
                save_parsed_object(data, reader)

            if skipped:
                log(f"{source.name}: not found, skipped " + ", ".join(f"{n} {k}" for k, n in skipped.items())
                    + " (rescan in Settings if this stays)")
            for entry in atlas_cache.values():
                if entry["dirty"]:
                    entry["data"].image = entry["image"]
                    save_parsed_object(entry["data"], entry["reader"])

            save_env(env, source)
        except Exception:
            shutil.copy2(original, source)
            raise

        manifest["last_mod_sha256"] = sha256_file(source)
        manifest["last_mod_stat"] = _file_sig(source)
        manifest["modified"] = datetime.now().isoformat(timespec="seconds")
        write_manifest(source, manifest)
    _record_mods_build()
    if progress:
        progress(1.0, "Done.")


def _file_sig(path: Path):
    st = path.stat()
    return [st.st_size, int(st.st_mtime)]


def _game_build():
    app = find_app()
    return plist_value(app / "Contents/Info.plist", "CFBundleVersion") if app else None


def _record_mods_build():
    try:
        prefs = prefs_payload()
        build = _plist_value_uncached(find_app() / "Contents/Info.plist", "CFBundleVersion")
        if build and build != "unknown":
            prefs["mods_build"] = build
            write_json(PREFS_JSON, prefs)
    except Exception:
        pass


def mods_need_reapply(state=None):
    """Why the client's mods are missing from the game files, or None.

    "update"   PG3D was updated (new build, or a modded file disappeared).
    "restored" Steam put back original files (e.g. a file check) on the same build.
    """
    state = state or load_mods()
    srcs = sources_for_state(state)
    if not srcs:
        return None
    app = find_app()
    if not app:
        return None
    build = _plist_value_uncached(app / "Contents/Info.plist", "CFBundleVersion")
    recorded = prefs_payload().get("mods_build")
    if recorded and build not in (None, "unknown") and recorded != build:
        return "update"
    for src in srcs:
        path = Path(src)
        if not path.exists():
            return "update"
        m = load_manifest(path)
        if not m or not m.get("last_mod_sha256"):
            continue
        sig = _file_sig(path)
        if m.get("last_mod_stat") == sig:
            continue
        if sha256_file(path) != m["last_mod_sha256"]:
            return "restored"
        m["last_mod_stat"] = sig
        write_manifest(path, m)
    return None


_REQ = threading.local()  # per-request flags (set while handling one HTTP request)


def _exclusive(fn):
    """Game-file changes run on the job queue, one at a time. Called from
    anywhere else (a click), the change is queued and this returns right away."""
    def wrapper(*args, **kwargs):
        if not in_job_worker():
            # Nothing ahead and the game closed: just do it now (quick changes stay instant).
            if not jobs_pending() and not game_running() and not launch_busy() and _MUTATE_LOCK.acquire(blocking=False):
                try:
                    return fn(*args, **kwargs)
                finally:
                    _MUTATE_LOCK.release()
            label = args[1] if fn.__name__ == "mutate_state" and len(args) > 1 else fn.__name__.replace("_", " ").capitalize()
            r = start_job("change", lambda progress: (fn(*args, **kwargs), None)[1], label=str(label))
            return {"ok": True, "queued": r["queued"]}
        with _MUTATE_LOCK:
            return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper


@_exclusive
def mutate_state(mutator, label, progress=None):
    before = load_mods()
    after = copy.deepcopy(before)
    mutator(after)
    affected = sources_for_state(before) | sources_for_state(after)
    push_history(before, after, label)
    try:
        rebuild_sources(affected, after, progress)
    except Exception:
        save_mods(before)
        try:
            rebuild_sources(affected, before)
        except Exception as rollback_error:
            log(f"State rollback rebuild also failed: {rollback_error}")
        hist = read_json(HISTORY_JSON, default_history())
        if hist["undo"]:
            hist["undo"].pop()
        write_json(HISTORY_JSON, hist)
        raise
    return after


@_exclusive
def undo_change():
    hist = read_json(HISTORY_JSON, default_history())
    if not hist["undo"]:
        raise RuntimeError("Nothing to undo.")
    current = load_mods()
    entry = hist["undo"].pop()
    target = entry["state"]
    hist["redo"].append({"state": current, "label": entry.get("label", "Change"), "at": datetime.now().isoformat(timespec="seconds")})
    hist["redo"] = hist["redo"][-25:]
    affected = sources_for_state(current) | sources_for_state(target)
    save_mods(target)
    try:
        rebuild_sources(affected, target)
    except Exception:
        save_mods(current)
        raise
    write_json(HISTORY_JSON, hist)
    return {"ok": True, "label": entry.get("label", "Change")}


@_exclusive
def redo_change():
    hist = read_json(HISTORY_JSON, default_history())
    if not hist["redo"]:
        raise RuntimeError("Nothing to redo.")
    current = load_mods()
    entry = hist["redo"].pop()
    target = entry["state"]
    hist["undo"].append({"state": current, "label": entry.get("label", "Change"), "at": datetime.now().isoformat(timespec="seconds")})
    hist["undo"] = hist["undo"][-25:]
    affected = sources_for_state(current) | sources_for_state(target)
    save_mods(target)
    try:
        rebuild_sources(affected, target)
    except Exception:
        save_mods(current)
        raise
    write_json(HISTORY_JSON, hist)
    return {"ok": True, "label": entry.get("label", "Change")}


def apply_cursor(cursor_id, size, color=None, outline=None, outline_width=0):
    target = load_cursor_target() or scan_cursor_target()
    size = max(8.0, min(100.0, float(size)))
    cursor_image_path(cursor_id)  # validate
    color, outline, ow = _cursor_style(color, outline, outline_width)

    def change(state):
        state["cursor"] = {"cursor": cursor_id, "size": size, "target": target,
                           "color": color, "outline": outline, "outline_width": ow}
    mutate_state(change, f"Cursor: {cursor_id}")
    return {"ok": True}


def reset_cursor():
    def change(state):
        state["cursor"] = None
    mutate_state(change, "Reset cursor")
    return {"ok": True}


def find_gui_item(asset_id=None, key=None):
    data = gui_catalog()
    for item in data.get("items", []):
        if key and item.get("key") == key:
            return item
        if asset_id is not None and int(item.get("id", -1)) == int(asset_id):
            return item
    return None


def apply_gui_asset(asset_id, scale=100, hidden=False):
    item = find_gui_item(asset_id=asset_id)
    if not item:
        raise RuntimeError("That GUI asset ID is not in the current scan.")
    scale = max(10.0, min(100.0, float(scale)))
    op = {
        "key": item["key"], "asset_id": int(item["id"]), "source_file": item["source_file"],
        "relative_file": item["relative_file"], "path_id": int(item["path_id"]),
        "name": item.get("name", ""), "width": int(item["width"]), "height": int(item["height"]),
        "pivot_x": float(item.get("pivot_x", 0.5)), "pivot_y": float(item.get("pivot_y", 0.5)),
        "scale": scale, "hidden": bool(hidden),
    }

    def change(state):
        state["gui"][item["key"]] = op
    mutate_state(change, f"GUI #{item['id']} {'hide' if hidden else str(int(scale)) + '%'}")
    return {"ok": True}


ROLE_DIR = SUPPORT / "sprite_packs"


def role_library(role):
    data = read_json(ROLE_DIR / role / "library.json", {"items": []})
    return [x for x in data.get("items", []) if (ROLE_DIR / role / x.get("file", "")).is_file()]


def install_role_sprite(role, data_url, name):
    if role not in SPRITE_ROLES:
        raise RuntimeError("Unknown sprite type.")
    if "," not in str(data_url):
        raise RuntimeError("Invalid image upload.")
    raw = base64.b64decode(str(data_url).split(",", 1)[1])
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeError("Images must be smaller than 8 MB.")
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        raise RuntimeError("Use a PNG image.")
    im = trim_alpha(im)
    if im.width < 4 or im.height < 4:
        raise RuntimeError("That image is empty.")
    if max(im.size) > 512:
        im.thumbnail((512, 512), Image.Resampling.LANCZOS)
    folder = ROLE_DIR / role
    folder.mkdir(parents=True, exist_ok=True)
    item_id = hashlib.sha256(raw).hexdigest()[:12]
    im.save(folder / f"{item_id}.png")
    lib = read_json(folder / "library.json", {"items": []})
    title = re.sub(r"\.png$", "", str(name or ""), flags=re.I).replace("_", " ").replace("-", " ").strip()[:40]
    lib["items"] = [x for x in lib.get("items", []) if x.get("id") != item_id] + [
        {"id": item_id, "name": title or "Custom", "file": f"{item_id}.png"}]
    write_json(folder / "library.json", lib)
    return {"ok": True, "id": item_id}


def _role_in_use(role):
    return next((op.get("replace") for op in load_mods().get("gui", {}).values()
                 if op.get("role") == role and op.get("replace")), None)


def delete_role_sprite(role, item_id):
    if role not in SPRITE_ROLES:
        raise RuntimeError("Unknown sprite type.")
    used = _role_in_use(role)
    if used and used.get("id") == item_id:
        raise RuntimeError("That one is in use. Reset it first.")
    folder = ROLE_DIR / role
    lib = read_json(folder / "library.json", {"items": []})
    for x in lib.get("items", []):
        if x.get("id") == item_id:
            (folder / Path(x["file"]).name).unlink(missing_ok=True)
    lib["items"] = [x for x in lib.get("items", []) if x.get("id") != item_id]
    write_json(folder / "library.json", lib)
    return {"ok": True}


def role_items(role):
    return [x for x in gui_catalog().get("items", []) if x.get("role") == role]


def sprite_roles_payload():
    cat = gui_catalog()
    mods = load_mods()
    out = {}
    for role in SPRITE_ROLES:
        items = [x for x in cat.get("items", []) if x.get("role") == role]
        applied = next((op.get("replace") for op in mods.get("gui", {}).values() if op.get("role") == role and op.get("replace")), None)
        out[role] = {"count": len(items), "previews": [x["preview"] for x in items[:6]], "applied": applied,
                     "installed": [{"id": x["id"], "name": x["name"], "thumb": f"/api/sprites/file/{role}/{x['id']}.png"}
                                   for x in role_library(role)]}
    return {"scanned": bool(cat.get("generated")), "roles": out}


def apply_sprite_role(role, item_id, size=90):
    if role not in SPRITE_ROLES:
        raise RuntimeError("Unknown sprite type.")
    items = role_items(role)
    if not items:
        raise RuntimeError("PG3D's current build doesn't have a sprite for this that the client can find.")
    pack = next((x for x in role_library(role) if x["id"] == item_id), None)
    if not pack:
        raise RuntimeError("Install one first.")
    size = max(8.0, min(100.0, float(size)))
    rep = {"id": pack["id"], "file": pack["file"], "name": pack["name"], "size": size}

    def change(state):
        for item in items:
            state["gui"][item["key"]] = {
                "key": item["key"], "asset_id": int(item["id"]), "source_file": item["source_file"],
                "relative_file": item["relative_file"], "path_id": int(item["path_id"]),
                "name": item.get("name", ""), "width": int(item["width"]), "height": int(item["height"]),
                "pivot_x": float(item.get("pivot_x", 0.5)), "pivot_y": float(item.get("pivot_y", 0.5)),
                "scale": 100, "hidden": False, "role": role, "replace": rep,
            }
    mutate_state(change, f"{'Hit marker' if role == 'hitmarker' else 'Kill icon'}: {pack['name']}")
    return {"ok": True}


def reset_sprite_role(role):
    def change(state):
        state["gui"] = {k: v for k, v in state["gui"].items() if v.get("role") != role}
    mutate_state(change, f"Reset {'hit marker' if role == 'hitmarker' else 'kill icon'}")
    return {"ok": True}


def reset_gui_asset(asset_id):
    item = find_gui_item(asset_id=asset_id)
    if not item:
        raise RuntimeError("That GUI asset ID is not in the current scan.")

    def change(state):
        state["gui"].pop(item["key"], None)
    mutate_state(change, f"Reset GUI #{item['id']}")
    return {"ok": True}


def set_hud_member(asset_id, enabled):
    item = find_gui_item(asset_id=asset_id)
    if not item:
        raise RuntimeError("That GUI asset ID is not in the current scan.")
    meta = {
        "key": item["key"], "asset_id": int(item["id"]), "source_file": item["source_file"],
        "relative_file": item["relative_file"], "path_id": int(item["path_id"]),
        "name": item.get("name", ""), "width": int(item["width"]), "height": int(item["height"]),
        "pivot_x": float(item.get("pivot_x", 0.5)), "pivot_y": float(item.get("pivot_y", 0.5)),
        "scale": 100, "hidden": False,
    }

    def change(state):
        if enabled:
            state["hud_set"][item["key"]] = meta
        else:
            state["hud_set"].pop(item["key"], None)
    mutate_state(change, f"{'Add' if enabled else 'Remove'} GUI #{item['id']} {'to' if enabled else 'from'} HUD set")
    return {"ok": True}

def import_hud_selector_selection():
    if not HUD_SELECTOR_FILE.exists():
        raise RuntimeError("No HUD Selector selection was found. Open the separate HUD Asset Selector, select assets, and press Save Selection first.")
    data = read_json(HUD_SELECTOR_FILE, {})
    entries = data.get("selected", [])
    if not entries:
        raise RuntimeError("The saved HUD selection is empty.")
    valid = {}
    missing = 0
    for item in entries:
        try:
            source = Path(item["source_file"])
            if not source.exists():
                missing += 1
                continue
            key = str(item["key"])
            valid[key] = {
                "key": key,
                "asset_id": int(item.get("id", item.get("asset_id", 0)) or 0),
                "source_file": str(source),
                "relative_file": item.get("relative_file", ""),
                "path_id": int(item["path_id"]),
                "name": item.get("name", ""),
                "width": int(item.get("width", 0) or 0),
                "height": int(item.get("height", 0) or 0),
                "pivot_x": float(item.get("pivot_x", 0.5)),
                "pivot_y": float(item.get("pivot_y", 0.5)),
                "scale": 100,
                "hidden": False,
            }
        except Exception:
            missing += 1
    if not valid:
        raise RuntimeError("None of the saved HUD assets still exist in the current PG3D installation. Re-scan with HUD Asset Selector after updating the game.")

    def change(state):
        state["hud_set"] = valid
        state["hud_disabled"] = False
    mutate_state(change, f"Import HUD Selector set ({len(valid)} assets)")
    return {"ok": True, "count": len(valid), "missing": missing, "generated": data.get("generated")}


def set_hud_disabled(enabled):
    enabled = bool(enabled)
    mods = load_mods()
    if enabled and not mods.get("hud_set"):
        raise RuntimeError("Your HUD set is empty. Mark the gameplay HUD assets first, then enable No HUD.")

    def change(state):
        state["hud_disabled"] = enabled
    mutate_state(change, "No HUD on" if enabled else "No HUD off")
    return {"ok": True}


def reset_all_gui():
    def change(state):
        # Hit marker / kill icon swaps live in the Cursor tab and stay.
        state["gui"] = {k: v for k, v in state["gui"].items() if v.get("role")}
        state["hud_disabled"] = False
    mutate_state(change, "Reset HUD changes")
    return {"ok": True}


def clear_hud_set():
    def change(state):
        state["hud_set"] = {}
        state["hud_disabled"] = False
    mutate_state(change, "Clear HUD set")
    return {"ok": True}

def import_custom_cursor(data_url):
    if "," not in data_url:
        raise RuntimeError("Invalid image upload.")
    _, payload = data_url.split(",", 1)
    raw = base64.b64decode(payload)
    if len(raw) > 8 * 1024 * 1024:
        raise RuntimeError("Custom cursor must be smaller than 8 MB.")
    temp = RUNTIME / "custom_cursor_upload.tmp"
    temp.write_bytes(raw)
    try:
        im = Image.open(temp).convert("RGBA")
        if im.width > 2048 or im.height > 2048:
            raise RuntimeError("Custom cursor must be 2048×2048 or smaller.")
        im.save(CUSTOM_CURSOR, "PNG")
    finally:
        temp.unlink(missing_ok=True)
    return {"ok": True}



def ci_get(d: dict, key: str, default=None):
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return default


def ci_set(d: dict, key: str, value, create=True):
    for k in list(d.keys()):
        if k.lower() == key.lower():
            d[k] = value
            return
    if create:
        d[key] = value


def ci_delete(d: dict, key: str):
    for k in list(d.keys()):
        if k.lower() == key.lower():
            del d[k]


def ci_key(d: dict, key: str):
    for k in d.keys():
        if k.lower() == key.lower():
            return k
    return None


def steam_files():
    root = steam_root()
    return root / "config/loginusers.vdf", root / "registry.vdf"


def steam_config_file():
    return steam_root() / "config/config.vdf"


def steam_connection_log():
    return steam_root() / "logs/connection_log.txt"


def steam_account_id(steamid64):
    try:
        return int(str(steamid64)) & 0xFFFFFFFF
    except Exception:
        return None


def steam_log_tail(max_bytes=2 * 1024 * 1024):
    path = steam_connection_log()
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read().decode("utf-8", "ignore")
    except Exception as e:
        log(f"Could not read Steam connection log: {e}")
        return ""


def latest_logged_account_id():
    """Return Steam3 account id only when the latest connection state is Logged On."""
    if not steam_running():
        return None
    text = steam_log_tail()
    matches = list(re.finditer(r"\[(Logged On|Logged Off),[^\]]*\]\s+\[U:1:(\d+)\]", text))
    if not matches:
        return None
    state, account_id = matches[-1].group(1), matches[-1].group(2)
    return int(account_id) if state == "Logged On" else None


def steam_log_offset():
    try:
        return steam_connection_log().stat().st_size
    except Exception:
        return 0


def read_steam_log_from(offset):
    path = steam_connection_log()
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        start = 0 if size < int(offset or 0) else int(offset or 0)
        with path.open("rb") as f:
            f.seek(start)
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def find_steam_binary():
    candidates = [
        Path("/Applications/Steam.app/Contents/MacOS/steam_osx"),
        steam_root() / "Steam.AppBundle/Steam/Contents/MacOS/steam_osx",
    ]
    return next((p for p in candidates if p.exists()), None)


def start_steam_client():
    """Start Steam with its startup chooser/login dialog suppressed."""
    log("Starting Steam with -silent after saved-session state switch.")
    subprocess.Popen(
        ["/usr/bin/open", "-n", "-a", "Steam", "--args", "-silent"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

def registry_active_user():
    """Steam's ActiveProcess/ActiveUser (32-bit account id of the signed-in user), or None."""
    try:
        _, registry_path = steam_files()
        reg = ci_get(load_vdf(registry_path, {}), "Registry", {}) or {}
        node = reg
        for key in ("HKCU", "Software", "Valve", "Steam", "ActiveProcess"):
            node = ci_get(node, key, {}) or {}
        value = int(str(ci_get(node, "ActiveUser", "0") or "0"))
        return value or None
    except Exception:
        return None


# Steam's own logon result codes. Plain words like "credential" or "expired" also
# show up during a normal sign-in (token refreshes), so they are not used.
_STEAM_GUARD_RESULTS = ("'AccountLogonDenied'", "'AccountLoginDeniedNeedTwoFactor'", "'TwoFactorCodeMismatch'",
                        "'AccountLogonDeniedVerifiedEmailRequired'", "'TwoFactorActivationCodeMismatch'")
_STEAM_REJECT_RESULTS = ("'InvalidPassword'", "'AccessDenied'", "'AccountDisabled'", "'InvalidLoginAuthCode'",
                         "'Expired'", "'PasswordRequiredToKickSession'")


def _steam_login_state(target_id, text):
    """('ok'|'guard'|'rejected'|None, wrong_account_id) from every signal Steam gives."""
    current = latest_logged_account_id()
    if current == target_id or (steam_running() and registry_active_user() == target_id):
        return "ok", None
    tag = f"[U:1:{target_id}]"
    verdict = None
    for line in (text or "").splitlines():
        if "[Logged On," in line and tag in line:
            return "ok", None
        if "RecvMsgClientLogOnResponse()" in line and tag in line:
            if "'OK'" in line:
                return "ok", None
            if any(r in line for r in _STEAM_GUARD_RESULTS):
                verdict = "guard"
            elif any(r in line for r in _STEAM_REJECT_RESULTS):
                verdict = "rejected"
    return verdict, (current if current not in (None, target_id) else None)


def _wait_steam_login_state(target_id, offset, timeout):
    """Wait for Steam to sign in target_id. A failure code only counts if no
    success follows within a few seconds (Steam retries internally)."""
    deadline = time.time() + timeout
    wrong_id, bad, bad_since = None, None, None
    while time.time() < deadline:
        state, wrong = _steam_login_state(target_id, read_steam_log_from(offset))
        if state == "ok":
            return "ok", None
        wrong_id = wrong or wrong_id
        if state in ("guard", "rejected"):
            bad = state
            bad_since = bad_since or time.time()
            if time.time() - bad_since > 8:
                return bad, wrong_id
        time.sleep(0.35)
    return bad, wrong_id


def wait_for_steam_login(target_steamid64, offset, timeout=60):
    target_id = steam_account_id(target_steamid64)
    if target_id is None:
        return False, "Steam account ID is invalid."
    state, wrong_id = _wait_steam_login_state(target_id, offset, timeout)
    if state == "ok":
        return True, None
    if state in ("guard", "rejected"):
        return False, "Steam rejected the remembered login for this account."
    if wrong_id is not None:
        return False, "Steam logged into a different remembered account instead of the selected one."
    return False, "Steam could not auto-login to this saved account. Its remembered session may have expired; sign into that account once in Steam with Remember me enabled, then retry."


def load_vdf(path: Path, default=None):
    if not path.exists():
        return copy.deepcopy(default if default is not None else {})
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        return vdf.load(f)


def atomic_vdf_dump(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".pg3dclient.tmp")
    with temp.open("w", encoding="utf-8") as f:
        vdf.dump(data, f, pretty=True)
    os.replace(temp, path)



_CRED_LOCK = threading.RLock()
_CRED_WARNED = []
_CRED_RETRY_AT = {}
_KEYCHAIN_ERR = []


def _keychain_set(steamid64, password):
    try:
        r = subprocess.run(
            ["/usr/bin/security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE,
             "-a", str(steamid64), "-l", f"PG3D Mac Client — Steam {steamid64}", "-w", password],
            capture_output=True, text=True, timeout=10)
    except Exception as e:
        _KEYCHAIN_ERR[:] = [str(e)]
        return False
    if r.returncode != 0:
        _KEYCHAIN_ERR[:] = [(r.stderr or r.stdout or f"exit {r.returncode}").strip()[:200]]
    return r.returncode == 0


def _keychain_get(steamid64):
    try:
        r = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", str(steamid64), "-w"],
            capture_output=True, text=True, timeout=10)
    except Exception:
        return ""
    return r.stdout.rstrip("\n") if r.returncode == 0 else ""


def _keychain_delete(steamid64):
    try:
        subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", str(steamid64)],
                       capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def load_steam_credentials():
    """Credential metadata only (usernames, flags). Passwords live in the macOS Keychain.

    Older versions stored passwords in plain JSON; those are moved into the
    Keychain the first time this runs and removed from the file.
    """
    # Several threads read credentials at startup; one migration at a time, or
    # parallel `security` calls trip over each other and fail.
    with _CRED_LOCK:
        data = read_json(CREDENTIALS_JSON, {})
        if not isinstance(data, dict):
            data = {}
        migrated, failed = 0, []
        for sid, entry in list(data.items()):
            if isinstance(entry, dict) and entry.get("password") and time.time() >= _CRED_RETRY_AT.get(sid, 0):
                ok = _keychain_set(sid, str(entry["password"]))
                if not ok:
                    time.sleep(0.5)
                    ok = _keychain_set(sid, str(entry["password"]))
                if ok and _keychain_get(sid) == str(entry["password"]):
                    entry.pop("password", None)
                    entry["keychain"] = True
                    migrated += 1
                else:
                    failed.append(sid)
                    _CRED_RETRY_AT[sid] = time.time() + 600  # don't stall every read on a refusing Keychain
        if migrated:
            save_steam_credentials(data)
            log(f"Moved {migrated} saved Steam password(s) into the macOS Keychain.")
        if failed and not _CRED_WARNED:
            _CRED_WARNED.append(True)
            log(f"Keychain refused {len(failed)} saved login(s); they stay in the client's file for now. "
                f"Last error: {_KEYCHAIN_ERR[0] if _KEYCHAIN_ERR else 'unknown'}")
        return data


def save_steam_credentials(data):
    CREDENTIALS_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = CREDENTIALS_JSON.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, CREDENTIALS_JSON)
    try:
        os.chmod(CREDENTIALS_JSON, 0o600)
    except Exception:
        pass


def _has_password(entry):
    return isinstance(entry, dict) and bool(entry.get("keychain") or entry.get("password"))


def credential_for(steamid64):
    entry = load_steam_credentials().get(str(steamid64), {})
    if not isinstance(entry, dict):
        return {}
    entry = dict(entry)
    if entry.get("keychain") and not entry.get("password"):
        entry["password"] = _keychain_get(steamid64)
    return entry


def set_account_credentials(steamid64, username, password):
    info = steam_accounts()
    account = next((a for a in info.get('accounts', []) if a.get('steamid64') == str(steamid64)), None)
    if not account:
        raise RuntimeError('That Steam account is not currently known on this Mac.')
    username = str(username or '').strip()
    password = str(password or '')
    if not username:
        username = account.get('account_name', '')
    if not username:
        raise RuntimeError('Steam username is required.')
    if not password:
        raise RuntimeError('Steam password is required.')
    data = load_steam_credentials()
    old = credential_for(steamid64)
    trusted_at = old.get('trusted_at') if old.get('username') == username and old.get('password') == password else None
    if _keychain_set(steamid64, password):
        entry = {'username': username, 'keychain': True}
    else:
        log("Keychain unavailable; saving this login in the protected client file instead.")
        entry = {'username': username, 'password': password}
    if trusted_at:
        entry['trusted_at'] = trusted_at
    data[str(steamid64)] = entry
    save_steam_credentials(data)
    return {'ok': True, 'steamid64': str(steamid64), 'username': username}


def remove_account_credentials(steamid64):
    data = load_steam_credentials()
    data.pop(str(steamid64), None)
    save_steam_credentials(data)
    _keychain_delete(steamid64)
    return {'ok': True, 'steamid64': str(steamid64)}


def start_steam_with_credentials(target, username, password):
    exe = find_steam_binary()
    if not exe:
        raise RuntimeError('Steam executable was not found.')
    # Steam currently documents -login <username> [password], -rememberpassword and -silent.
    # The password is intentionally never written to our log.
    args = [str(exe), '-login', username, password, '-rememberpassword']
    log(f"Starting Steam credential login for {target.get('display_name') or target.get('account_name')} as {username!r}; password redacted.")
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def wait_for_credential_login(target, offset, timeout=90):
    target_id = steam_account_id(target.get('steamid64'))
    if target_id is None:
        return False, 'Steam account ID is invalid.'
    state, wrong_id = _wait_steam_login_state(target_id, offset, timeout)
    if state == 'ok':
        return True, None
    if state == 'guard':
        return False, 'Steam Guard or another Steam authentication check is required for this account.'
    if state == 'rejected':
        return False, "Steam rejected the saved username/password for this account."
    if wrong_id is not None:
        return False, 'Steam logged into a different account instead of the selected one.'
    return False, 'Steam did not complete the credential login for the selected account.'


def login_selected_account_with_credentials(target):
    cred = credential_for(target.get('steamid64'))
    username = str(cred.get('username') or '').strip()
    password = str(cred.get('password') or '')
    if not username or not password:
        return None
    if game_running():
        raise RuntimeError('Close Pixel Gun 3D before switching Steam accounts.')
    set_status(launch_phase='switching', launch_message=f"Signing Steam into {target['display_name']}…", launch_error=None)
    quit_steam_gracefully()
    time.sleep(0.8)

    # Disable the chooser so a credential launch is not redirected to Who's Playing.
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = ACCOUNT_BACKUPS / ('credentials_' + stamp)
    backup_dir.mkdir(parents=True, exist_ok=True)
    try:
        disable_steam_user_chooser(backup_dir)
    except Exception as e:
        log(f'Could not disable Steam chooser before credential login: {e}')

    offset = steam_log_offset()
    start_steam_with_credentials(target, username, password)
    deadline = time.time() + 25
    while time.time() < deadline and not steam_running():
        time.sleep(0.35)
    if not steam_running():
        return {'switched': True, 'account': target, 'needs_login': True,
                'message': 'Steam did not start after the credential login request.'}

    ok, reason = wait_for_credential_login(target, offset, timeout=90)
    if not ok:
        needs_guard = bool(reason and ('Steam Guard' in reason or 'authentication check is required' in reason))
        return {'switched': True, 'account': target, 'needs_login': True,
                'needs_guard': needs_guard,
                'message': reason or 'Steam did not authenticate the selected account.'}
    log(f"Steam credential login verified for {target.get('account_name')} / {target.get('steamid64')}")
    return {'switched': True, 'account': target, 'needs_login': False, 'needs_guard': False}

def steam_accounts():
    return copy.deepcopy(cached("steam_accounts", 3.0, _steam_accounts_uncached))


def _steam_accounts_uncached():
    login_path, registry_path = steam_files()
    if not login_path.exists():
        return {"accounts": [], "selected": None, "current": None, "error": "Steam loginusers.vdf was not found."}
    data = load_vdf(login_path, {})
    users = ci_get(data, "users", {}) or {}
    current_name = None
    try:
        registry = load_vdf(registry_path, {})
        reg = ci_get(registry, "Registry", {}) or {}
        hkcu = ci_get(reg, "HKCU", {}) or {}
        software = ci_get(hkcu, "Software", {}) or {}
        valve = ci_get(software, "Valve", {}) or {}
        steam = ci_get(valve, "Steam", {}) or {}
        current_name = ci_get(steam, "AutoLoginUser")
    except Exception as e:
        log(f"Could not read Steam registry.vdf: {e}")

    live_account_id = latest_logged_account_id()
    accounts = []
    for sid64, info in users.items():
        if not isinstance(info, dict):
            continue
        account = str(ci_get(info, "AccountName", "") or "")
        persona = str(ci_get(info, "PersonaName", account) or account)
        if not account:
            continue
        # Prefer Steam's live connection log. Registry/VDF values can describe
        # the account Steam intends to use even while the client is logged out.
        if live_account_id is not None:
            active = steam_account_id(sid64) == live_account_id
        elif steam_running():
            active = bool(current_name and account.lower() == str(current_name).lower())
        else:
            active = False
        remember = str(ci_get(info, "RememberPassword", "0")) == "1"
        allow_auto = str(ci_get(info, "AllowAutoLogin", "0")) == "1" or str(ci_get(info, "AutoLogin", "0")) == "1"
        accounts.append({
            "steamid64": str(sid64), "account_name": account, "persona_name": persona,
            "active": active, "remembered": remember, "auto_login": allow_auto,
        })
    prefs = read_json(PREFS_JSON, {})
    nicknames = prefs.get("account_nicknames", {})
    if not isinstance(nicknames, dict):
        nicknames = {}
    credentials = load_steam_credentials()
    for item in accounts:
        nickname = str(nicknames.get(item["steamid64"], "") or "").strip()
        item["nickname"] = nickname
        item["display_name"] = nickname or item["persona_name"]
        cred = credentials.get(item["steamid64"], {}) if isinstance(credentials, dict) else {}
        item["credentials_saved"] = bool(isinstance(cred, dict) and cred.get("username") and _has_password(cred))
        item["credential_username"] = str(cred.get("username", "")) if isinstance(cred, dict) else ""
    accounts.sort(key=lambda a: (not a["active"], a["display_name"].lower()))
    selected = prefs.get("selected_steamid")
    if selected not in {a["steamid64"] for a in accounts}:
        selected = next((a["steamid64"] for a in accounts if a["active"]), accounts[0]["steamid64"] if accounts else None)
    current = next((a["steamid64"] for a in accounts if a["active"]), None)
    return {"accounts": accounts, "selected": selected, "current": current, "error": None}


def select_account(steamid64):
    info = steam_accounts()
    if steamid64 not in {a["steamid64"] for a in info["accounts"]}:
        raise RuntimeError("That Steam account is not currently remembered by Steam on this Mac.")
    prefs = read_json(PREFS_JSON, {})
    prefs["selected_steamid"] = steamid64
    write_json(PREFS_JSON, prefs)
    return {"ok": True}


def set_account_nickname(steamid64, nickname):
    info = steam_accounts()
    if steamid64 not in {a["steamid64"] for a in info.get("accounts", [])}:
        raise RuntimeError("That Steam account is not currently remembered on this Mac.")
    nickname = str(nickname or "").strip()
    if len(nickname) > 40:
        raise RuntimeError("Nickname must be 40 characters or fewer.")
    prefs = prefs_payload()
    nicknames = prefs.get("account_nicknames", {})
    if not isinstance(nicknames, dict):
        nicknames = {}
    if nickname:
        nicknames[steamid64] = nickname
    else:
        nicknames.pop(steamid64, None)
    prefs["account_nicknames"] = nicknames
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "steamid64": steamid64, "nickname": nickname}


def _steam_process_pids():
    pids=set()
    for cmd in (
        ["/usr/bin/pgrep","-x","steam_osx"],
        ["/usr/bin/pgrep","-x","steamwebhelper"],
        ["/usr/bin/pgrep","-f","/Steam.app/Contents/MacOS/steam_osx"],
        ["/usr/bin/pgrep","-f","/Steam.app/Contents/Frameworks/Steam Helper"],
    ):
        try:
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=3)
            if r.returncode in (0,1):
                for line in r.stdout.splitlines():
                    try:
                        pid=int(line.strip())
                        if pid>1 and pid!=os.getpid(): pids.add(pid)
                    except Exception: pass
        except Exception: pass
    return pids


def _wait_steam_stopped(timeout):
    deadline=time.time()+timeout
    while time.time()<deadline:
        if not _steam_process_pids(): return True
        time.sleep(0.15)
    return not _steam_process_pids()


def _signal_steam(sig):
    for pid in list(_steam_process_pids()):
        try: os.kill(pid,sig)
        except ProcessLookupError: pass
        except Exception as e: log(f"Could not signal Steam pid {pid}: {e}")


def quit_steam_tcno_style():
    """Stop Steam completely before writing its login state."""
    if not _steam_process_pids(): return
    try:
        subprocess.run(["/usr/bin/osascript","-e",'tell application "Steam" to quit'],
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=4)
    except Exception: pass
    if _wait_steam_stopped(8): return
    import signal
    _signal_steam(signal.SIGTERM)
    if _wait_steam_stopped(6): return
    _signal_steam(signal.SIGKILL)
    if not _wait_steam_stopped(3):
        raise RuntimeError("Steam could not be fully closed. Quit Steam manually and retry.")


def _set_ci_existing(mapping,key,value):
    if not isinstance(mapping,dict): return False
    real=ci_key(mapping,key)
    if real is None: return False
    mapping[real]=str(value)
    return True


def write_steam_switch_state_tcno(target_steamid64,target_account_name,offline=False):
    """Independent Python implementation of the state transition used by TcNo Account Switcher on macOS."""
    login_path,registry_path=steam_files()
    config_path=steam_config_file()
    if not login_path.exists():
        raise RuntimeError("Steam loginusers.vdf was not found on this Mac.")

    login_data=load_vdf(login_path,{})
    users=ci_get(login_data,'users',{}) or {}
    selected_key=next((str(k) for k in users.keys() if str(k)==str(target_steamid64)),None)
    if selected_key is None or not isinstance(users.get(selected_key),dict):
        raise RuntimeError("The selected Steam account is not present in Steam's remembered-account file.")

    try:
        raw=login_path.read_bytes()
        if raw: (login_path.with_name('loginusers.vdf_last')).write_bytes(raw)
    except Exception as e:
        log(f"loginusers.vdf_last backup warning: {e}")

    for sid,user in users.items():
        if not isinstance(user,dict): continue
        selected=str(sid)==str(selected_key)
        v='1' if selected else '0'
        # TcNo writes all three. Older PG3D client builds incorrectly chose one schema.
        ci_set(user,'MostRecent',v)
        ci_set(user,'AutoLogin',v)
        ci_set(user,'RememberPassword',v)
        if ci_get(user,'WantsOfflineMode') is not None:
            ci_set(user,'WantsOfflineMode','1' if (selected and offline) else '0')
        if ci_get(user,'WantsOffline') is not None:
            ci_set(user,'WantsOffline','1' if (selected and offline) else '0')
        if ci_get(user,'SkipOfflineModeWarning') is not None:
            ci_set(user,'SkipOfflineModeWarning','1' if (selected and offline) else '0')
        if ci_get(user,'SkipOfflineWarn') is not None:
            ci_set(user,'SkipOfflineWarn','1' if (selected and offline) else '0')
    atomic_vdf_dump(login_path,login_data)

    registry=load_vdf(registry_path,{})
    steam_reg=nested_registry_steam(registry)
    ci_set(steam_reg,'AutoLoginUser',str(target_account_name))
    ci_set(steam_reg,'RememberPassword','1')
    atomic_vdf_dump(registry_path,registry)

    # TcNo only updates this chooser preference if Steam already stores it.
    if config_path.exists():
        try:
            config=load_vdf(config_path,{})
            steam_cfg=nested_config_steam(config)
            if ci_get(steam_cfg,'NoSavePersonalInfo') is not None and str(ci_get(steam_cfg,'NoSavePersonalInfo','0'))=='1':
                raise RuntimeError("Steam is configured not to save login information. Disable that Steam setting first.")
            if _set_ci_existing(steam_cfg,'AlwaysShowUserChooser','0'):
                atomic_vdf_dump(config_path,config)
        except RuntimeError:
            raise
        except Exception as e:
            log(f"AlwaysShowUserChooser update warning: {e}")

    # Re-read and verify every critical field before Steam starts.
    check=load_vdf(login_path,{})
    cusers=ci_get(check,'users',{}) or {}
    for sid,user in cusers.items():
        if not isinstance(user,dict): continue
        want='1' if str(sid)==str(selected_key) else '0'
        for field in ('MostRecent','AutoLogin','RememberPassword'):
            got=str(ci_get(user,field,'') or '')
            if got!=want:
                raise RuntimeError(f"Steam switch verification failed: {field} did not persist for account {sid}.")
    regcheck=load_vdf(registry_path,{})
    rsteam=nested_registry_steam(regcheck)
    if str(ci_get(rsteam,'AutoLoginUser','') or '').lower()!=str(target_account_name).lower():
        raise RuntimeError("Steam switch verification failed: registry.vdf AutoLoginUser did not persist.")
    if str(ci_get(rsteam,'RememberPassword','') or '')!='1':
        raise RuntimeError("Steam switch verification failed: registry.vdf RememberPassword did not persist.")
    log(f"TcNo-style Steam state verified for {target_account_name} ({target_steamid64}).")


def start_steam_direct():
    exe=find_steam_binary()
    if not exe: raise RuntimeError("Steam executable was not found.")
    subprocess.Popen([str(exe)],cwd=str(steam_root()),start_new_session=True,
                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def quit_steam_gracefully(timeout=22):
    if not steam_running():
        return
    try:
        subprocess.run(["open", "steam://exit"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
    except Exception:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not steam_running():
            return
        time.sleep(0.4)
    try:
        subprocess.run(["/usr/bin/osascript", "-e", 'tell application "Steam" to quit'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        pass
    deadline = time.time() + 8
    while time.time() < deadline:
        if not steam_running():
            return
        time.sleep(0.4)
    raise RuntimeError("Steam did not close cleanly. Close Steam manually, then try launching again.")


def nested_registry_steam(registry):
    reg = ci_get(registry, "Registry")
    if not isinstance(reg, dict):
        reg = {}; registry["Registry"] = reg
    hkcu = ci_get(reg, "HKCU")
    if not isinstance(hkcu, dict):
        hkcu = {}; reg["HKCU"] = hkcu
    software = ci_get(hkcu, "Software")
    if not isinstance(software, dict):
        software = {}; hkcu["Software"] = software
    valve = ci_get(software, "Valve")
    if not isinstance(valve, dict):
        valve = {}; software["Valve"] = valve
    steam = ci_get(valve, "Steam")
    if not isinstance(steam, dict):
        steam = {}; valve["Steam"] = steam
    return steam


def nested_config_steam(config):
    install = ci_get(config, "InstallConfigStore")
    if not isinstance(install, dict):
        install = {}; config["InstallConfigStore"] = install
    software = ci_get(install, "Software")
    if not isinstance(software, dict):
        software = {}; install["Software"] = software
    valve = ci_get(software, "Valve")
    if not isinstance(valve, dict):
        valve = {}; software["Valve"] = valve
    steam = ci_get(valve, "Steam")
    if not isinstance(steam, dict):
        steam = {}; valve["Steam"] = steam
    return steam


def disable_steam_user_chooser(backup_dir: Path):
    """Disable Steam's explicit startup account chooser and verify the write."""
    path = steam_config_file()
    config = load_vdf(path, {})
    if path.exists():
        shutil.copy2(path, backup_dir / "config.vdf")
    steam = nested_config_steam(config)
    previous = ci_get(steam, "AlwaysShowUserChooser")
    no_save = str(ci_get(steam, "NoSavePersonalInfo", "0") or "0")
    if no_save == "1":
        raise RuntimeError(
            "Steam is set to not save account credentials. Disable 'Don't save account credentials on this computer' in Steam first."
        )
    ci_set(steam, "AlwaysShowUserChooser", "0")
    atomic_vdf_dump(path, config)

    check = load_vdf(path, {})
    check_steam = nested_config_steam(check)
    if str(ci_get(check_steam, "AlwaysShowUserChooser", "")) != "0":
        raise RuntimeError("Steam's account-chooser setting could not be changed safely.")
    log(f"Steam AlwaysShowUserChooser: {previous!r} -> '0'.")


def reorder_users_target_first(login_data: dict, selected_key: str):
    """Put the selected account first without changing any account tokens."""
    users_key = ci_key(login_data, "users") or "users"
    users = login_data.get(users_key, {})
    if not isinstance(users, dict) or selected_key not in users:
        return users
    ordered = {selected_key: users[selected_key]}
    for sid, entry in users.items():
        if sid != selected_key:
            ordered[sid] = entry
    login_data[users_key] = ordered
    return ordered


def write_current_steam_account_state(login_path: Path, registry_path: Path, selected_key: str, target: dict):
    """Select one remembered Steam account using the schema present on this Mac."""
    login_data = load_vdf(login_path, {})
    users = ci_get(login_data, "users", {}) or {}
    if selected_key not in users:
        raise RuntimeError("Selected Steam account disappeared from loginusers.vdf.")

    user_dicts = [u for u in users.values() if isinstance(u, dict)]
    uses_auto = any(ci_get(u, "AutoLogin") is not None for u in user_dicts)
    uses_most = any(ci_get(u, "MostRecent") is not None for u in user_dicts)
    modern = uses_auto or not uses_most
    uses_allow = any(ci_get(u, "AllowAutoLogin") is not None for u in user_dicts)
    now = str(int(time.time()))

    for sid, user in users.items():
        if not isinstance(user, dict):
            continue
        is_target = str(sid) == str(selected_key)
        value = "1" if is_target else "0"
        if modern:
            ci_set(user, "AutoLogin", value)
            ci_delete(user, "MostRecent")
        else:
            ci_set(user, "MostRecent", value)
        if uses_allow:
            ci_set(user, "AllowAutoLogin", value)
        if is_target:
            if ci_get(user, "Timestamp") is not None:
                ci_set(user, "Timestamp", now)
            if ci_get(user, "WantsOfflineMode") is not None:
                ci_set(user, "WantsOfflineMode", "0")
            if ci_get(user, "SkipOfflineModeWarning") is not None:
                ci_set(user, "SkipOfflineModeWarning", "0")

    reorder_users_target_first(login_data, selected_key)
    atomic_vdf_dump(login_path, login_data)

    registry = load_vdf(registry_path, {})
    steam_reg = nested_registry_steam(registry)
    ci_set(steam_reg, "AutoLoginUser", target["account_name"])
    atomic_vdf_dump(registry_path, registry)

    verify_login = load_vdf(login_path, {})
    verify_users = ci_get(verify_login, "users", {}) or {}
    verify_target = verify_users.get(str(selected_key))
    if not isinstance(verify_target, dict):
        raise RuntimeError("Steam account switch verification failed: target account was not written.")
    selector_name = "AutoLogin" if modern else "MostRecent"
    if str(ci_get(verify_target, selector_name, "0")) != "1":
        raise RuntimeError("Steam account switch verification failed: target account is not marked for auto-login.")
    for sid, user in verify_users.items():
        if str(sid) == str(selected_key) or not isinstance(user, dict):
            continue
        if str(ci_get(user, selector_name, "0") or "0") == "1":
            raise RuntimeError("Steam account switch verification failed: more than one account is marked active.")

    verify_registry = load_vdf(registry_path, {})
    verify_name = ci_get(nested_registry_steam(verify_registry), "AutoLoginUser")
    if str(verify_name or "").lower() != str(target["account_name"]).lower():
        raise RuntimeError("Steam account switch verification failed: AutoLoginUser does not match the selected account.")

    log(
        "Steam account state verified: "
        f"account={target['account_name']} steamid={selected_key} schema={selector_name}"
    )
    return selector_name



def _open_accessibility_settings():
    """Open the exact macOS privacy pane needed for Steam chooser control."""
    for url in (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility",
    ):
        try:
            subprocess.Popen(["/usr/bin/open", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass


def _open_automation_settings():
    """Open macOS Privacy & Security → Automation."""
    for url in (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Automation",
    ):
        try:
            subprocess.Popen(["/usr/bin/open", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass


def _set_plist_value(plist_path: Path, key: str, value: str, kind="string"):
    """Best-effort Info.plist adjustment for the compiled AppleScript helper."""
    pb = Path("/usr/libexec/PlistBuddy")
    if not pb.exists() or not plist_path.exists():
        return
    set_cmd = [str(pb), "-c", f"Set :{key} {value}", str(plist_path)]
    r = subprocess.run(set_cmd, capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.run([str(pb), "-c", f"Add :{key} {kind} {value}", str(plist_path)],
                       capture_output=True, text=True)


def ensure_account_helper():
    """Build the stable macOS helper with the built-in osacompile tool.

    Unlike v1.3.1 this requires no Xcode, clang, or Command Line Tools.
    macOS' own AppleScript compiler is present on standard macOS installs.
    """
    if not ACCOUNT_HELPER_SOURCE.is_file():
        raise RuntimeError("The bundled Steam helper source is missing.")

    source_hash = hashlib.sha256(b"helper-v3-permissions\0" + ACCOUNT_HELPER_SOURCE.read_bytes()).hexdigest()
    old_hash = ""
    try:
        old_hash = ACCOUNT_HELPER_HASH.read_text(encoding="utf-8").strip()
    except Exception:
        pass

    applet = ACCOUNT_HELPER_APP / "Contents/MacOS/applet"
    if applet.is_file() and old_hash == source_hash:
        return ACCOUNT_HELPER_APP

    try:
        if ACCOUNT_HELPER_APP.exists():
            shutil.rmtree(ACCOUNT_HELPER_APP)
    except Exception as e:
        raise RuntimeError(f"Could not replace the old Steam helper: {e}")

    compiler = Path("/usr/bin/osacompile")
    if not compiler.exists():
        raise RuntimeError("macOS' built-in AppleScript compiler (/usr/bin/osacompile) is unavailable.")

    r = subprocess.run(
        [str(compiler), "-o", str(ACCOUNT_HELPER_APP), str(ACCOUNT_HELPER_SOURCE)],
        capture_output=True, text=True, timeout=45,
    )
    if r.returncode != 0 or not applet.is_file():
        detail = (r.stderr or r.stdout or "unknown osacompile error").strip()
        log("AppleScript account helper build failed: " + detail)
        raise RuntimeError("The Steam helper could not be prepared with macOS' built-in AppleScript compiler.")

    plist_path = ACCOUNT_HELPER_APP / "Contents/Info.plist"
    _set_plist_value(plist_path, "CFBundleIdentifier", "local.pg3d.accounthelper.v3")
    _set_plist_value(plist_path, "NSAppleEventsUsageDescription", "PG3D Account Helper uses System Events only to select the chosen Steam account and enter a one-time Steam Guard code.")
    _set_plist_value(plist_path, "CFBundleName", "PG3D Account Helper")
    _set_plist_value(plist_path, "CFBundleDisplayName", "PG3D Account Helper")
    _set_plist_value(plist_path, "LSUIElement", "true", "bool")

    # Ad-hoc signing is optional, but makes the helper's TCC identity more stable.
    try:
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--deep", str(ACCOUNT_HELPER_APP)],
                       capture_output=True, text=True, timeout=20)
    except Exception as e:
        log(f"AppleScript helper signing warning: {e}")

    ACCOUNT_HELPER_HASH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNT_HELPER_HASH.write_text(source_hash, encoding="utf-8")
    return ACCOUNT_HELPER_APP


def _run_account_helper(command: str, values=None, timeout=30):
    """Run the compiled helper via a private 0600 request/result channel.

    This avoids putting Steam Guard codes in command-line arguments or logs.
    The request file is deleted by the helper as soon as it reads it.
    """
    helper_app = ensure_account_helper()
    values = list(values or [])
    safe_values = [str(v).replace("\r", " ").replace("\n", " ") for v in values]
    request = "\n".join([str(command)] + safe_values) + "\n"

    for p in (ACCOUNT_HELPER_REQUEST, ACCOUNT_HELPER_RESULT):
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass

    tmp = ACCOUNT_HELPER_REQUEST.with_suffix(".tmp")
    tmp.write_text(request, encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    os.replace(tmp, ACCOUNT_HELPER_REQUEST)

    try:
        r = subprocess.run(["/usr/bin/open", "-W", "-n", str(helper_app)],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("PG3D Account Helper timed out while waiting for Steam.")
    finally:
        try:
            ACCOUNT_HELPER_REQUEST.unlink(missing_ok=True)
        except Exception:
            pass

    output = ""
    try:
        if ACCOUNT_HELPER_RESULT.exists():
            output = ACCOUNT_HELPER_RESULT.read_text(encoding="utf-8", errors="replace").strip()
    finally:
        try:
            ACCOUNT_HELPER_RESULT.unlink(missing_ok=True)
        except Exception:
            pass

    if not output and r.returncode != 0:
        output = (r.stderr or r.stdout or "").strip()
    return output


def _check_helper_accessibility():
    """Check both macOS permissions used by the AppleScript helper.

    Accessibility controls the Steam UI.
    Automation / Apple Events allows the helper to talk to System Events.
    v1.3.2 incorrectly reported both failures as Accessibility.
    """
    output = _run_account_helper("CHECK", timeout=15)
    normalized = str(output or "").strip().upper()

    if normalized == "TRUSTED":
        return True, "TRUSTED"

    if normalized == "AUTOMATION_REQUIRED":
        _open_automation_settings()
        return False, "AUTOMATION_REQUIRED"

    if normalized == "ACCESSIBILITY_REQUIRED":
        _open_accessibility_settings()
        return False, "ACCESSIBILITY_REQUIRED"

    low = str(output or "").lower()
    if "not authorized to send apple events" in low or "apple events" in low:
        _open_automation_settings()
        return False, "AUTOMATION_REQUIRED"
    if "assistive access" in low or "accessibility" in low or "not authorized" in low:
        _open_accessibility_settings()
        return False, "ACCESSIBILITY_REQUIRED"

    return False, str(output or "UNKNOWN_PERMISSION_ERROR")

def native_select_steam_account(target: dict):
    """Activate the requested account in Steam's chooser through the stable helper app."""
    names = []
    for value in (target.get("persona_name"), target.get("account_name")):
        value = str(value or "").strip()
        if value and value.lower() not in {x.lower() for x in names}:
            names.append(value)
    if not names:
        return False, "no target name"

    try:
        trusted, detail = _check_helper_accessibility()
        if not trusted:
            return False, detail
        output = _run_account_helper("SELECT", names, timeout=24)
    except Exception as e:
        log(f"Steam chooser AppleScript helper failed: {e}")
        return False, str(e)

    log(f"Steam chooser AppleScript helper result={output!r}")
    if output == "ACTIVATION_SENT":
        return True, output
    if output == "ACCESSIBILITY_REQUIRED":
        _open_accessibility_settings()
        return False, "accessibility required"
    return False, output or "not found"


def _applescript_literal(value: str):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def try_press_steam_chooser_account(target: dict):
    """Best-effort fallback for a visible Steam chooser using accessibility text."""
    names = []
    for value in (target.get("persona_name"), target.get("account_name"), target.get("display_name")):
        value = str(value or "").strip()
        if value and value.lower() not in {n.lower() for n in names}:
            names.append(value)
    if not names:
        return False, "no target name"
    tests = " or ".join(f'txt contains {_applescript_literal(name)}' for name in names)
    script = f'''
on pressCandidate(theElement)
    tell application "System Events"
        try
            perform action "AXPress" of theElement
            return true
        end try
        try
            click theElement
            return true
        end try
    end tell
    return false
end pressCandidate

tell application "System Events"
    if UI elements enabled is false then return "ACCESSIBILITY_REQUIRED"
    if not (exists process "Steam") then return "NO_STEAM"
    tell process "Steam"
        set frontmost to true
        try
            set elems to entire contents of windows
        on error
            return "NO_WINDOWS"
        end try
        repeat with el in elems
            set txt to ""
            try
                set txt to name of el as text
            end try
            if txt is "" then
                try
                    set txt to value of el as text
                end try
            end if
            if txt is "" then
                try
                    set txt to description of el as text
                end try
            end if
            if txt is not "" and ({tests}) then
                if my pressCandidate(el) then return "PRESSED"
                set parentEl to el
                repeat 5 times
                    try
                        set parentEl to parent of parentEl
                        if my pressCandidate(parentEl) then return "PRESSED_PARENT"
                    on error
                        exit repeat
                    end try
                end repeat
            end if
        end repeat
    end tell
end tell
return "NOT_FOUND"
'''
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True, text=True, timeout=12,
        )
        output = (result.stdout or result.stderr or "").strip()
        log(f"Steam chooser accessibility fallback: rc={result.returncode} result={output!r}")
        if output in ("PRESSED", "PRESSED_PARENT"):
            return True, output
        if "not allowed assistive access" in output.lower() or output == "ACCESSIBILITY_REQUIRED":
            return False, "accessibility required"
        return False, output or "not found"
    except Exception as e:
        log(f"Steam chooser accessibility fallback failed: {e}")
        return False, str(e)


def wait_for_target_or_handle_chooser(target: dict, offset: int, timeout=80):
    """Verify the selected SteamID; activate its chooser tile when Steam presents the account chooser."""
    target_id = steam_account_id(target["steamid64"])
    if target_id is None:
        return False, "Steam account ID is invalid."
    deadline = time.time() + timeout
    fallback_attempts = 0
    fallback_detail = None
    last_fallback_at = 0.0
    wrong_id = None
    started = time.time()
    while time.time() < deadline:
        current = latest_logged_account_id()
        if current == target_id:
            return True, None
        if current is not None and current != target_id:
            wrong_id = current
            break

        text = read_steam_log_from(offset)
        if text:
            for line in text.splitlines():
                if f"[U:1:{target_id}]" in line:
                    if "RecvMsgClientLogOnResponse()" in line and "'OK'" in line:
                        return True, None
                    if "[Logged On," in line:
                        return True, None
                    if any(x in line for x in ("Access Denied", "Invalid Password", "Account Logon Denied", "Expired")):
                        return False, "Steam rejected the remembered login for this account."

        elapsed = time.time() - started
        if fallback_attempts < 3 and elapsed >= 6 and time.time() - last_fallback_at >= 4.0:
            fallback_attempts += 1
            last_fallback_at = time.time()
            pressed, fallback_detail = native_select_steam_account(target)
            log(f"Steam chooser activation attempt {fallback_attempts}: activated={pressed} detail={fallback_detail}")
            if fallback_detail == "accessibility required":
                return False, (
                    "macOS needs permission for PG3D Account Helper to control Steam. "
                    "System Settings has been opened. Enable PG3D Account Helper under Privacy & Security → Accessibility, then press Start again."
                )
        time.sleep(0.35)

    if wrong_id is not None:
        return False, "Steam logged into a different saved account; PG3D was not started."
    if fallback_detail == "accessibility required":
        return False, (
            "Steam showed its account chooser. Enable PG3D Account Helper under System Settings → Privacy & Security → Accessibility, then press Start again."
        )
    return False, (
        "Steam did not confirm the selected account. Its remembered Steam session may need to be refreshed once with Remember me enabled."
    )


def switch_steam_account_if_needed():
    """Switch via Steam's remembered token using the TcNo-style macOS state flow."""
    info=steam_accounts()
    if info.get('error'): raise RuntimeError(info['error'])
    selected=info.get('selected')
    if not selected: return {'switched':False,'account':None,'needs_login':False}
    target=next((a for a in info.get('accounts',[]) if a.get('steamid64')==selected),None)
    if not target: raise RuntimeError('Selected Steam account is no longer present.')
    if target.get('active'): return {'switched':False,'account':target,'needs_login':False}
    if game_running(): raise RuntimeError('Close Pixel Gun 3D before switching Steam accounts.')
    if not target.get('remembered'):
        # No remembered Steam token: fall back to the login saved in Accounts.
        cred_result = login_selected_account_with_credentials(target)
        if cred_result is not None:
            return cred_result
        raise RuntimeError('This Steam account has no remembered login on this Mac. Save its Steam login in Accounts, or log into it once in Steam with Remember password enabled, then retry.')

    set_status(launch_phase='switching',launch_message=f"Switching Steam to {target['display_name']}…",launch_error=None)
    quit_steam_tcno_style()
    time.sleep(0.6)

    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir=ACCOUNT_BACKUPS/('tcno_'+stamp)
    backup_dir.mkdir(parents=True,exist_ok=True)
    login_path,registry_path=steam_files(); config_path=steam_config_file()
    for path,name in ((login_path,'loginusers.vdf'),(registry_path,'registry.vdf'),(config_path,'config.vdf')):
        if path.exists():
            try: shutil.copy2(path,backup_dir/name)
            except Exception as e: log(f"Steam backup warning {name}: {e}")

    try:
        write_steam_switch_state_tcno(selected,target['account_name'],offline=False)
    except Exception:
        for path,name in ((login_path,'loginusers.vdf'),(registry_path,'registry.vdf'),(config_path,'config.vdf')):
            src=backup_dir/name
            if src.exists():
                try: shutil.copy2(src,path)
                except Exception as e: log(f"Steam rollback warning {name}: {e}")
        raise

    offset=steam_log_offset()
    start_steam_direct()
    deadline=time.time()+30
    while time.time()<deadline and not steam_running(): time.sleep(0.25)
    if not steam_running(): raise RuntimeError('Steam did not start after the account switch.')

    ok,reason=wait_for_steam_login(selected,offset,timeout=90)
    if not ok:
        # The remembered token didn't work (expired or revoked): try the saved login.
        log(f"Remembered-token switch failed for {target['account_name']}: {reason}")
        cred_result = login_selected_account_with_credentials(target)
        if cred_result is not None:
            return cred_result
        raise RuntimeError(reason or 'Steam started but did not confirm the selected account. Save its Steam login in Accounts, or sign into it once in Steam with Remember password enabled, then retry.')
    log(f"Steam confirmed selected account {target['account_name']} / {selected}.")
    return {'switched':True,'account':target,'needs_login':False}

def _set_pending_guard(target, launch_kind, fps=None):
    global PENDING_GUARD
    with _LOCK:
        PENDING_GUARD = {
            'steamid64': str(target.get('steamid64') or ''),
            'launch_kind': str(launch_kind),
            'fps': fps,
            'created_at': time.time(),
        }
    set_status(
        launch_phase='steam_guard',
        launch_message='Enter the Steam Guard code sent to your email.',
        launch_error=None,
    )


def _clear_pending_guard():
    global PENDING_GUARD
    with _LOCK:
        PENDING_GUARD = None


def _mark_guard_authorized(steamid64):
    """Record only that this Mac completed Guard once; never store the one-time code."""
    data = load_steam_credentials()
    entry = data.get(str(steamid64), {})
    if isinstance(entry, dict):
        entry['trusted_at'] = datetime.now().isoformat(timespec='seconds')
        data[str(steamid64)] = entry
        save_steam_credentials(data)


def _submit_guard_with_native_helper(code):
    try:
        trusted, detail = _check_helper_accessibility()
        if not trusted:
            if detail == "AUTOMATION_REQUIRED":
                raise RuntimeError(
                    "Enable PG3D Account Helper → System Events in System Settings → Privacy & Security → Automation, then submit the code again."
                )
            if detail == "ACCESSIBILITY_REQUIRED":
                raise RuntimeError(
                    "Enable PG3D Account Helper in System Settings → Privacy & Security → Accessibility, then submit the code again."
                )
            raise RuntimeError("PG3D Account Helper permission check failed: " + str(detail))
        output = _run_account_helper("GUARD", [code], timeout=25)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Steam Guard helper could not be prepared: {e}")

    log(f"Steam Guard AppleScript helper result={output!r}")
    if output == "AUTOMATION_REQUIRED":
        _open_automation_settings()
        raise RuntimeError(
            "Enable PG3D Account Helper → System Events in System Settings → Privacy & Security → Automation, then submit the code again."
        )
    if output == "ACCESSIBILITY_REQUIRED":
        _open_accessibility_settings()
        raise RuntimeError(
            "Enable PG3D Account Helper in System Settings → Privacy & Security → Accessibility, then submit the code again."
        )
    if output != "GUARD_SUBMITTED":
        if output == "GUARD_FIELD_NOT_FOUND":
            raise RuntimeError("Steam Guard code field was not found. Leave Steam's verification window open and try again.")
        if output == "GUARD_FIELD_WRITE_FAILED":
            raise RuntimeError("Steam's verification field was found but could not be filled. Check Accessibility permission and try again.")
        if output.startswith("ERROR:"):
            raise RuntimeError("Steam Guard helper error: " + output)
        raise RuntimeError(output or "Steam Guard code could not be submitted to Steam.")


def wait_after_guard_submit(target, offset, timeout=75):
    """Wait for this exact SteamID after a submitted Guard code.

    Unlike the initial credential wait, seeing the words "Steam Guard" again is
    not itself an error; Steam can log the challenge while processing a valid
    response. Only explicit rejection/expiry is treated as failure.
    """
    target_id = steam_account_id(target.get('steamid64'))
    if target_id is None:
        return False, 'Steam account ID is invalid.'
    deadline = time.time() + timeout
    wrong_id = None
    while time.time() < deadline:
        current = latest_logged_account_id()
        if current == target_id:
            return True, None
        if current is not None and current != target_id:
            wrong_id = current

        text = read_steam_log_from(offset)
        lower = text.lower()
        if text:
            for line in text.splitlines():
                if f'[U:1:{target_id}]' in line:
                    if 'RecvMsgClientLogOnResponse()' in line and "'OK'" in line:
                        return True, None
                    if '[Logged On,' in line:
                        return True, None
            explicit_bad = (
                'invalid authenticator' in lower or
                'invalid steam guard' in lower or
                'incorrect steam guard' in lower or
                'code expired' in lower or
                'expired code' in lower or
                'account logon denied invalidauthcode' in lower or
                'accountlogondeniedinvalidauthcode' in lower
            )
            if explicit_bad:
                return False, 'Steam rejected that Steam Guard code. Use the newest code from the email.'
        time.sleep(0.35)
    if wrong_id is not None:
        return False, 'Steam logged into a different account instead of the selected one.'
    return False, 'Steam did not finish the Steam Guard verification. Keep the Steam verification window open and try the newest code.'


def submit_steam_guard_code(raw_code):
    global PENDING_GUARD
    code = re.sub(r'[^A-Za-z0-9]', '', str(raw_code or '')).upper()
    if not 4 <= len(code) <= 12:
        raise RuntimeError('Enter the Steam Guard code from the newest email.')

    with _LOCK:
        pending = copy.deepcopy(PENDING_GUARD)
    if not pending:
        raise RuntimeError('There is no Steam Guard login waiting for a code.')
    if time.time() - float(pending.get('created_at') or 0) > 15 * 60:
        _clear_pending_guard()
        raise RuntimeError('That Steam Guard login expired. Start the selected account again to request a new code.')

    info = steam_accounts()
    target = next((a for a in info.get('accounts', [])
                   if a.get('steamid64') == pending.get('steamid64')), None)
    if not target:
        _clear_pending_guard()
        raise RuntimeError('The Steam account waiting for authorization is no longer available.')

    set_status(launch_phase='steam_guard', launch_message='Verifying Steam Guard…', launch_error=None)
    offset = steam_log_offset()
    _submit_guard_with_native_helper(code)
    ok, reason = wait_after_guard_submit(target, offset, timeout=75)
    if not ok:
        # Keep the pending challenge so a newer email code can be submitted without restarting everything.
        set_status(launch_phase='steam_guard', launch_message='Steam Guard verification is still required.', launch_error=None)
        raise RuntimeError(reason or 'Steam did not accept the Steam Guard code.')

    _mark_guard_authorized(target.get('steamid64'))
    launch_kind = pending.get('launch_kind') or 'normal'
    fps = pending.get('fps')
    _clear_pending_guard()

    # Steam has now created/updated its own remembered authorization for this Mac.
    # Continue the launch without re-running the account switch and causing another Guard challenge.
    if launch_kind == 'fps':
        game = find_app()
        if not game:
            raise RuntimeError('Pixel Gun 3D.app was not found.')
        set_status(launch_phase='launching', launch_message='Starting PG3D through FPS unlocker…', launch_error=None)
        result = fps_launch(game, fps if fps is not None else prefs_payload().get('fps_target', 'uncapped'))
        set_status(launch_phase='idle', launch_message=result['message'], launch_error=None,
                   last_fps={**result, 'at': datetime.now().isoformat(timespec='seconds')})
        return {'ok': True, 'launched': True, 'needs_guard': False, **result}

    set_status(launch_phase='launching', launch_message='Starting PG3D normally…', launch_error=None)
    set_status(launch_phase='idle', launch_message=start_game_normally(), launch_error=None)
    return {'ok': True, 'launched': True, 'needs_guard': False}

def launch_normal():
    if game_running():
        raise RuntimeError("Pixel Gun 3D is already running.")
    account_result = switch_steam_account_if_needed()
    if account_result.get('needs_guard'):
        target = account_result.get('account') or {}
        _set_pending_guard(target, 'normal')
        return {'ok': True, 'needs_guard': True, 'needs_login': True,
                'message': 'Enter the Steam Guard code sent to your email.'}
    if account_result.get("needs_login"):
        msg = account_result.get("message") or "Steam needs you to finish signing in to the selected account."
        set_status(launch_phase="needs_login", launch_message=msg, launch_error=None)
        return {"ok": True, "needs_login": True, "message": msg + " PG3D was not launched."}
    set_status(launch_phase="launching", launch_message="Starting PG3D normally…", launch_error=None)
    set_status(launch_phase="idle", launch_message=start_game_normally(), launch_error=None)
    return {"ok": True, "needs_login": False}


def fps_target_value(value):
    if value in (None, "", "uncapped", "0", 0, -1, "-1"):
        return "-1"
    try:
        n = int(value)
    except Exception:
        raise RuntimeError("FPS target must be 30–1000 or uncapped.")
    if not 30 <= n <= 1000:
        raise RuntimeError("FPS target must be 30–1000 or uncapped.")
    return str(n)


def binary_arches(path: Path):
    """CPU architectures in a Mach-O executable (thin or universal)."""
    names = {0x01000007: "x86_64", 0x0100000C: "arm64"}
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return set()
    magic = int.from_bytes(head[:4], "big")
    if magic in (0xCAFEBABE, 0xCAFEBABF):  # universal (32- or 64-bit offsets)
        count = int.from_bytes(head[4:8], "big")
        step = 20 if magic == 0xCAFEBABE else 32
        return {names.get(int.from_bytes(head[8 + i * step:12 + i * step], "big"), "?") for i in range(min(count, 10))}
    if head[:4] == b"\xcf\xfa\xed\xfe":
        return {names.get(int.from_bytes(head[4:8], "little"), "?")}
    return set()


def mouse_fix_enabled():
    return prefs_payload().get("mouse_fix", True) is not False


def set_mouse_fix_pref(on):
    prefs = prefs_payload()
    prefs["mouse_fix"] = bool(on)
    prefs["mouse_fix_failures"] = 0
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "mouse_fix": bool(on)}


def mouse_fix_usable(binary: Path):
    prepare_fps_runtime()
    return (mouse_fix_enabled() and MOUSEFIX_DYLIB.is_file()
            and bool(binary_arches(binary) & {"x86_64", "arm64"}))


def _watch_mouse_fix_start():
    """If PG3D keeps closing right after starting with the mouse fix, turn the fix off."""
    def work():
        time.sleep(25)
        # Started directly but never opened a window: hung. Quit it and use Steam next time.
        waited = 25
        while _game_running_uncached() and pg3d_window_count() == 0 and waited < 60:
            time.sleep(5)
            waited += 5
        if _game_running_uncached() and pg3d_window_count() == 0:
            log("PG3D started with the mouse fix but never opened a window; quitting it and turning the fix off")
            quit_game()
            prefs = prefs_payload()
            prefs["mouse_fix"] = False
            write_json(PREFS_JSON, prefs)
            set_status(launch_error="PG3D got stuck while starting with the mouse fix, so the fix was turned off. "
                                    "Press Start again.")
            return
        prefs = prefs_payload()
        if _game_running_uncached():
            if prefs.get("mouse_fix_failures"):
                prefs["mouse_fix_failures"] = 0
                write_json(PREFS_JSON, prefs)
            return
        fails = int(prefs.get("mouse_fix_failures", 0)) + 1
        prefs["mouse_fix_failures"] = fails
        if fails >= 2:
            prefs["mouse_fix"] = False
            set_status(launch_error="PG3D closed right after starting twice in a row, so the mouse fix was turned off. "
                                    "Turn it back on in Settings › Troubleshooting.")
            log("Mouse fix disabled after two quick exits")
        write_json(PREFS_JSON, prefs)
    threading.Thread(target=work, daemon=True).start()


def ensure_steam_running(timeout=60):
    """Direct launches (FPS unlocker, mouse fix) skip steam://run, which used to start Steam.
    PG3D needs a running, signed-in Steam, so start it first if needed."""
    if _steam_running_uncached():
        return True
    start_steam_client()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _steam_running_uncached():
            time.sleep(8)  # let it finish signing in before the game asks for it
            return True
        time.sleep(1)
    return False


def start_game_normally():
    """Start PG3D at its own frame rate: directly with the mouse fix when it can
    be used, otherwise through Steam. Returns the message to show."""
    game = find_app()
    binary = game / "Contents/MacOS/Pixel Gun 3D" if game else None
    if binary and mouse_fix_usable(binary) and FPS_LAUNCHER.is_file():
        if ensure_steam_running():
            try:
                launch_with_mouse_fix(game)
                return "PG3D is running with the mouse fix."
            except Exception as e:
                log(f"Mouse-fix start failed, starting through Steam instead: {e}")
        else:
            log("Mouse-fix start skipped: Steam didn't come up in time.")
    elif binary:
        log(f"Mouse fix not used: enabled={mouse_fix_enabled()} dylib={MOUSEFIX_DYLIB.is_file()} "
            f"launcher={FPS_LAUNCHER.is_file()} arches={sorted(binary_arches(binary))}")
    subprocess.Popen(["open", f"steam://run/{STEAM_APP_ID}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "PG3D is running."


def launch_with_mouse_fix(game: Path):
    """Start PG3D directly with only the mouse fix loaded (normal frame pacing)."""
    binary = game / "Contents/MacOS/Pixel Gun 3D"
    log_path = FPS_LOG_DIR / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    log_path.write_text(f"[PG3D Mac Client] launch {datetime.now().isoformat(timespec='seconds')} mouse fix only\n",
                        encoding="utf-8")
    # "-1" leaves the frame rate alone (the FPS library isn't loaded); "1" keeps Unity's own display-link path.
    result = subprocess.run([str(FPS_LAUNCHER), str(binary), str(MOUSEFIX_DYLIB), "-1", str(log_path), "1"],
                            cwd=str(game.parent), capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(f"Starting PG3D failed: {(result.stderr or result.stdout).strip()}")
    _watch_mouse_fix_start()
    return {"log": str(log_path)}


def fps_launch(game: Path, fps: str):
    prepare_fps_runtime()
    missing = []
    if not FPS_DYLIB.is_file():
        missing.append(str(FPS_DYLIB))
    if not FPS_LAUNCHER.is_file():
        missing.append(str(FPS_LAUNCHER))
    if missing:
        log("FPS bundle lookup failed. BASE=" + str(BASE) + " missing=" + repr(missing))
        raise RuntimeError("FPS unlocker bundle could not be found in this app copy. Quit the client completely and reopen it.")
    if not os.access(FPS_LAUNCHER, os.X_OK):
        FPS_LAUNCHER.chmod(FPS_LAUNCHER.stat().st_mode | 0o111)
    binary = game / "Contents/MacOS/Pixel Gun 3D"
    if not binary.exists():
        raise RuntimeError("PG3D executable was not found inside the app bundle.")
    fps = fps_target_value(fps)
    if not ensure_steam_running():
        raise RuntimeError("Steam didn't start. Open Steam, sign in, then press Start again.")
    log_path = FPS_LOG_DIR / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    log_path.write_text(
        f"[PG3D Mac Client] launch {datetime.now().isoformat(timespec='seconds')} fps={fps}\n",
        encoding="utf-8"
    )
    use_mouse_fix = mouse_fix_usable(binary)
    dylibs = f"{FPS_DYLIB}:{MOUSEFIX_DYLIB}" if use_mouse_fix else str(FPS_DYLIB)
    result = subprocess.run(
        [str(FPS_LAUNCHER), str(binary), dylibs, fps, str(log_path), "0"],
        cwd=str(game.parent), capture_output=True, text=True, timeout=10
    )
    if use_mouse_fix and result.returncode == 0:
        _watch_mouse_fix_start()
    if result.returncode != 0:
        tail = ""
        try:
            tail = "\n".join(log_path.read_text(errors="ignore").splitlines()[-15:])
        except Exception:
            pass
        raise RuntimeError(f"FPS launcher failed: {(result.stderr or result.stdout).strip()}\n{tail}".strip())
    pid_text = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    try:
        pid = int(pid_text)
    except Exception:
        pid = None
    state = _fps_log_state(log_path, pid, 20)
    if state is None:
        # Still loading: keep watching in the background and update the message then.
        threading.Thread(target=_finish_fps_check, args=(log_path, pid), daemon=True).start()
    else:
        _record_fps_build_result(state == "ok" if state != "exited" else None)
    return {"pid": pid, "verified": state == "ok", "log": str(log_path), "message": _FPS_MESSAGES[state]}


_FPS_MESSAGES = {
    "ok": "FPS unlocker active.",
    "fail": "The FPS unlocker couldn't hook into this PG3D build; the game runs at its own frame rate.",
    "exited": "PG3D closed before the FPS unlocker could start.",
    None: "PG3D is loading; the FPS unlocker switches on once the game is ready.",
}


def _fps_log_state(log_path, pid, timeout):
    """'ok' / 'fail' / 'exited' from the unlocker's own log, or None if undecided yet.
    Only the frame-rate hook counts: v-sync is optional and is reset every 5 s anyway."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            txt = log_path.read_text(errors="ignore")
        except Exception:
            txt = ""
        if "render pacing override is active" in txt or "target frame rate override installed" in txt:
            return "ok"
        if ("target frame rate binding does not match" in txt or "target frame rate wrapper RVA" in txt
                or "required Unity binding unavailable" in txt):
            return "fail"
        if pid is not None:
            try:
                os.kill(pid, 0)
            except Exception:
                return "exited"
        time.sleep(0.25)
    return None


def _finish_fps_check(log_path, pid):
    state = _fps_log_state(log_path, pid, 120)
    _record_fps_build_result(state == "ok" if state in ("ok", "fail") else None)
    if state in ("ok", "fail") and STATUS.get("launch_phase") == "idle":
        set_status(launch_message=_FPS_MESSAGES[state])


def _record_fps_build_result(ok):
    """Remember per PG3D build whether the unlocker managed to attach (None: unknown)."""
    if ok is None:
        return
    try:
        build = str(plist_value(find_app() / "Contents/Info.plist", "CFBundleVersion") or "")
    except Exception:
        build = ""
    if not build or build == "unknown":
        return
    prefs = prefs_payload()
    results = prefs.get("fps_builds_v2") if isinstance(prefs.get("fps_builds_v2"), dict) else {}
    results[build] = bool(ok)
    prefs["fps_builds_v2"] = dict(list(results.items())[-20:])
    write_json(PREFS_JSON, prefs)


def fps_build_match(build):
    """True: works on this build, False: failed on it, None: not tried yet."""
    if build in (None, "unknown"):
        return None
    known = prefs_payload().get("fps_builds_v2") or {}
    if str(build) in known:
        return bool(known[str(build)])
    return True if build in FPS_SUPPORTED_BUILDS else None


def launch_with_fps(fps):
    if game_running():
        raise RuntimeError("Pixel Gun 3D is already running.")
    app = find_app()
    if not app:
        raise RuntimeError("Pixel Gun 3D.app was not found.")
    account_result = switch_steam_account_if_needed()
    if account_result.get('needs_guard'):
        target = account_result.get('account') or {}
        _set_pending_guard(target, 'fps', fps)
        return {'ok': True, 'needs_guard': True, 'needs_login': True,
                'message': 'Enter the Steam Guard code sent to your email.'}
    if account_result.get("needs_login"):
        msg = account_result.get("message") or "Steam needs you to finish signing in to the selected account."
        set_status(launch_phase="needs_login", launch_message=msg, launch_error=None)
        return {"ok": True, "needs_login": True, "message": msg + " PG3D was not launched."}
    set_status(launch_phase="launching", launch_message="Starting PG3D through FPS unlocker…", launch_error=None)
    result = fps_launch(app, fps)
    set_status(launch_phase="idle", launch_message=result["message"], launch_error=None,
               last_fps={**result, "at": datetime.now().isoformat(timespec="seconds")})
    return {"ok": True, "needs_login": False, **result}


LAUNCH_BUSY = ("preparing", "switching", "updating", "launching", "starting")
LAUNCH_DEFAULT_TIMES = {"switch": 35.0, "start_normal": 25.0, "start_fps": 15.0}
_LAUNCH = {"stage": None}


def launch_busy():
    return STATUS.get("launch_phase") in LAUNCH_BUSY


def _launch_times():
    """Expected seconds per launch step: the median of the last starts, so one
    unusually slow start (a game update, a Steam prompt) doesn't skew it."""
    t = dict(LAUNCH_DEFAULT_TIMES)
    samples = prefs_payload().get("launch_samples") or {}
    for k in t:
        vals = sorted(float(v) for v in (samples.get(k) or []) if isinstance(v, (int, float)))
        if vals:
            t[k] = vals[len(vals) // 2]
    return t


def _learn_launch_time(key, seconds):
    prefs = prefs_payload()
    samples = prefs.get("launch_samples") if isinstance(prefs.get("launch_samples"), dict) else {}
    vals = [v for v in (samples.get(key) or []) if isinstance(v, (int, float))]
    vals.append(round(min(float(seconds), 600.0), 1))
    samples[key] = vals[-6:]
    prefs["launch_samples"] = samples
    write_json(PREFS_JSON, prefs)


def _launch_stage(name, lo, hi, expected, message, later=0.0):
    """Enter a launch stage; the ticker moves the % within [lo, hi] by elapsed/expected."""
    _LAUNCH["stage"] = {"lo": lo, "hi": hi, "expected": max(0.1, expected), "start": time.time(), "later": later}
    set_status(launch_phase=name, launch_message=message, launch_error=None)
    _launch_tick()


def _launch_tick():
    st = _LAUNCH.get("stage")
    if not st:
        return
    el = time.time() - st["start"]
    exp = st["expected"]
    if el <= exp:
        frac = 0.9 * el / exp
        eta = (exp - el) + st["later"]
    else:
        # Past the usual time: keep creeping forward and say so instead of a fake number.
        frac = 0.9 + 0.09 * (1 - exp / el)
        eta = -1 if not st["later"] else st["later"]
    set_status(launch_progress=int(st["lo"] + (st["hi"] - st["lo"]) * frac), launch_eta=int(round(eta)))


def _launch_ticker():
    while launch_busy():
        _launch_tick()
        time.sleep(0.4)


def start_launch(kind, fps=None):
    with _LOCK:
        if launch_busy():
            raise RuntimeError("PG3D is already starting.")
        if game_running():
            raise RuntimeError("Pixel Gun 3D is already running.")
        if kind == "fps" and not find_app():
            raise RuntimeError("Pixel Gun 3D.app was not found.")
        STATUS.update(launch_phase="preparing", launch_progress=0, launch_eta=None,
                      launch_message="Getting ready…", launch_error=None)
    threading.Thread(target=_launch_job, args=(kind, fps), daemon=True).start()
    threading.Thread(target=_launch_ticker, daemon=True).start()
    return {"ok": True, "started": True}


def _launch_job(kind, fps):
    times = _launch_times()
    start_key = "start_fps" if kind == "fps" else "start_normal"
    try:
        # 1) Never start while game files are being rewritten (theme, sky, cursor).
        _launch_stage("preparing", 0, 8, 1, "Checking game files…", later=times["switch"] + times[start_key])
        waited = time.time()
        _LAUNCH["waiting_jobs"] = True  # lets queued changes run first instead of waiting for this start
        try:
            while jobs_pending() or _MUTATE_LOCK.locked():
                set_status(launch_message="Applying your changes first… " + (STATUS.get("job_message") or ""))
                if time.time() - waited > 1800:
                    raise RuntimeError("Texture changes are taking too long. Try again when they finish.")
                _LAUNCH["stage"]["start"] = time.time()  # the start itself hasn't begun yet
                time.sleep(0.5)
        finally:
            _LAUNCH["waiting_jobs"] = False

        # 2) Make sure the selected Steam account is the one signed in.
        info = steam_accounts()
        target = next((a for a in info.get("accounts", []) if a.get("steamid64") == info.get("selected")), None)
        needs_switch = bool(target and not target.get("active"))
        name = (target or {}).get("display_name") or "your account"
        _launch_stage("switching", 8, 55 if needs_switch else 12, times["switch"] if needs_switch else 0.5,
                      f"Signing in to Steam as {name}…" if needs_switch else f"Steam is signed in as {name}.",
                      later=times[start_key])
        t0 = time.time()
        account_result = switch_steam_account_if_needed()
        if account_result.get("switched"):
            _learn_launch_time("switch", time.time() - t0)
        if account_result.get("needs_guard"):
            _set_pending_guard(account_result.get("account") or {}, kind, fps)
            _LAUNCH["stage"] = None
            set_status(launch_phase="steam_guard", launch_message="Enter the Steam Guard code sent to your email.",
                       launch_progress=None, launch_eta=None)
            return
        if account_result.get("needs_login"):
            raise RuntimeError((account_result.get("message") or "Steam needs you to finish signing in.") + " PG3D was not launched.")

        # 3) A pending PG3D update installs first, then the client's mods go back on.
        upd = game_update_info()
        if upd.get("available"):
            _launch_stage("updating", 12, 40, 120, "Steam is updating Pixel Gun 3D…", later=times[start_key])
            if not upd.get("updating"):
                subprocess.Popen(["/usr/bin/open", f"steam://validate/{STEAM_APP_ID}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            began = time.time()
            while True:
                time.sleep(2)
                invalidate("game_update")
                u = game_update_info()
                if u.get("progress") is not None:
                    set_status(launch_message=f"Steam is updating Pixel Gun 3D… {u['progress']}%")
                if not u.get("available") and not u.get("updating") and time.time() - began > 6:
                    break
                if time.time() - began > 45 * 60:
                    raise RuntimeError("The PG3D update is taking very long. Check Steam's Downloads page.")
        if reapply_enabled():
            reason = mods_need_reapply()
            if reason:
                _launch_stage("updating", 40, 52, 25, "Re-applying your mods…", later=times[start_key])
                with _MUTATE_LOCK:
                    _reapply_work(lambda f, m: set_status(launch_message=m), reason)

        # 4) Start the game and wait until it is actually running.
        _launch_stage("launching", 55 if needs_switch else 12, 96, times[start_key],
                      "Starting PG3D through the FPS unlocker…" if kind == "fps" else "Starting PG3D…")
        t0 = time.time()
        if kind == "fps":
            result = fps_launch(find_app(), fps if fps is not None else prefs_payload().get("fps_target", "uncapped"))
            message = result["message"]
            set_status(last_fps={**result, "at": datetime.now().isoformat(timespec="seconds")})
        else:
            message = start_game_normally()
        # The first start after a game update can take a few minutes while Steam
        # finishes up, so wait patiently and say what's going on.
        deadline = time.time() + 300
        nudged = False
        while not _game_running_uncached():
            if time.time() > deadline:
                raise RuntimeError("Steam was asked to start PG3D, but the game didn't open within 5 minutes. "
                                   "Check Steam for a prompt or a running update.")
            if not nudged and time.time() - t0 > 75:
                nudged = True
                set_status(launch_message="Still waiting for Steam to open PG3D… check Steam if a prompt is showing.")
            time.sleep(0.5)
        _learn_launch_time(start_key, time.time() - t0)
        _LAUNCH["stage"] = None
        set_status(launch_phase="idle", launch_progress=100, launch_eta=0, launch_message=message, launch_error=None)
    except Exception as e:
        log(traceback.format_exc())
        _LAUNCH["stage"] = None
        set_status(launch_phase="idle", launch_progress=None, launch_eta=None, launch_message="", launch_error=str(e))


def default_overlay_config():
    return {
        "enabled": True,
        "hotkey": {"code":"F8","key":"F8","display":"F8","mods":{"shift":False,"ctrl":False,"alt":False,"cmd":False}},
        # The v1.7 UI presents this as Unified vs Individual. Internally Unified
        # maps to group.enabled=True, Individual to False.
        "layout": "free",
        "anchor": "top-right",
        "group": {
            "enabled": True,
            "orientation": "vertical",
            "size_mode": "bundle",
            "size": 18,
            "color": "#FFFFFF",
            "x": 0.5,
            "y": 0.5,
            "placed": False,
        },
        "items": [
            {"id":"fps","enabled":True,"size":18,"color":"#FFFFFF","order":0,"x":0.5,"y":0.5,"placed":False},
            {"id":"clock","enabled":True,"size":18,"color":"#FFFFFF","order":1,"x":0.5,"y":0.5,"placed":False,
             "clock":{"format24":True,"format12":False,"calendar":False,"seconds":False}},
            {"id":"ping","enabled":True,"size":18,"color":"#FFFFFF","order":2,"x":0.5,"y":0.5,"placed":False},
            {"id":"music","enabled":True,"size":16,"color":"#FFFFFF","order":3,"x":0.5,"y":0.5,"placed":False,
             "music":{"lyrics":False,"cover":True,"cover_size":48,"title":True,"artist":True,"album":False,"timestamp":True}},
            {"id":"rank","enabled":False,"size":16,"color":"#FFFFFF","order":4,"x":0.5,"y":0.5,"placed":False},
        ],
    }


def overlay_config():
    raw = read_json(OVERLAY_CONFIG_JSON, None)
    return _validate_overlay_config(raw if isinstance(raw, dict) else {})


def _validate_hotkey(raw):
    default = default_overlay_config()["hotkey"]
    if isinstance(raw, str):
        return {"code":raw,"key":raw,"display":raw,"mods":{"shift":False,"ctrl":False,"alt":False,"cmd":False}}
    if not isinstance(raw, dict):
        return copy.deepcopy(default)
    code = str(raw.get("code") or default["code"])[:40]
    key = str(raw.get("key") or code)[:40]
    display = str(raw.get("display") or key or code)[:80]
    mods = raw.get("mods") if isinstance(raw.get("mods"), dict) else {}
    return {"code":code,"key":key,"display":display,"mods":{m:bool(mods.get(m,False)) for m in ("shift","ctrl","alt","cmd")}}


def _validate_overlay_config(raw):
    base = default_overlay_config()
    if not isinstance(raw, dict):
        return base
    base["enabled"] = bool(raw.get("enabled", True))
    base["background"] = bool(raw.get("background", True))
    base["hotkey"] = _validate_hotkey(raw.get("hotkey"))
    # v1.7 always renders from saved free positions. The explicit Freemove editor
    # is how positions are changed.
    base["layout"] = "free"

    gsrc = raw.get("group") if isinstance(raw.get("group"), dict) else {}
    group = base["group"]
    group["enabled"] = bool(gsrc.get("enabled", group["enabled"]))
    group["orientation"] = gsrc.get("orientation") if gsrc.get("orientation") in ("horizontal","vertical") else "vertical"
    group["size_mode"] = "bundle" if group["enabled"] else "individual"
    try: group["size"] = max(10, min(48, int(gsrc.get("size", group["size"]))))
    except Exception: pass
    color = str(gsrc.get("color", group["color"])).upper()
    if re.fullmatch(r"#[0-9A-F]{6}", color): group["color"] = color
    try: group["x"] = max(0.0, min(0.98, float(gsrc.get("x", group["x"]))))
    except Exception: pass
    try: group["y"] = max(0.0, min(0.98, float(gsrc.get("y", group["y"]))))
    except Exception: pass
    group["placed"] = bool(gsrc.get("placed", group["placed"]))

    templates = {x["id"]: x for x in default_overlay_config()["items"]}
    incoming = raw.get("items") if isinstance(raw.get("items"), list) else []
    out = []
    for idx, item_id in enumerate(("fps","clock","ping","music","rank")):
        src = next((x for x in incoming if isinstance(x,dict) and x.get("id")==item_id), {})
        item = copy.deepcopy(templates[item_id])
        item["enabled"] = bool(src.get("enabled", item["enabled"]))
        try: item["size"] = max(10, min(48, int(src.get("size", item["size"]))))
        except Exception: pass
        color = str(src.get("color", item["color"])).upper()
        if re.fullmatch(r"#[0-9A-F]{6}", color): item["color"] = color
        try: item["order"] = int(src.get("order", idx))
        except Exception: item["order"] = idx
        for pos in ("x","y"):
            try: item[pos] = max(0.0, min(0.98, float(src.get(pos, item[pos]))))
            except Exception: pass
        item["placed"] = bool(src.get("placed", item.get("placed",False)))
        if item_id == "music":
            msrc = src.get("music") if isinstance(src.get("music"),dict) else {}
            mc = item["music"]
            for flag in ("lyrics","cover","title","artist","album","timestamp"):
                mc[flag] = bool(msrc.get(flag, mc[flag]))
            # Album text is intentionally supported by the runtime for backwards
            # compatibility but hidden from the v1.7 UI.
            mc["album"] = False
            try: mc["cover_size"] = max(24, min(120, int(msrc.get("cover_size", mc["cover_size"]))))
            except Exception: pass
        if item_id == "clock":
            csrc = src.get("clock") if isinstance(src.get("clock"),dict) else {}
            cc = item["clock"]
            cc["format24"] = bool(csrc.get("format24", cc["format24"]))
            cc["format12"] = bool(csrc.get("format12", cc["format12"]))
            if cc["format24"] == cc["format12"]:
                cc["format24"], cc["format12"] = True, False
            cc["calendar"] = bool(csrc.get("calendar", cc["calendar"]))
            cc["seconds"] = bool(csrc.get("seconds", cc["seconds"]))
        out.append(item)
    out.sort(key=lambda x:x.get("order",0))
    for i,item in enumerate(out): item["order"] = i
    base["items"] = out
    return base


def set_overlay_config(raw):
    cfg = _validate_overlay_config(raw)
    write_json(OVERLAY_CONFIG_JSON, cfg)
    if any(x.get("id") == "rank" and x.get("enabled") for x in cfg["items"]):
        threading.Thread(target=write_overlay_rank, args=(_playtime_account(),), daemon=True).start()
    ensure_overlay_process()
    return {"ok": True, "config": cfg}


def reset_overlay_config():
    cfg = default_overlay_config()
    write_json(OVERLAY_CONFIG_JSON, cfg)
    ensure_overlay_process()
    return {"ok": True, "config": cfg}


def begin_overlay_editor(raw=None):
    cfg = set_overlay_config(raw if isinstance(raw,dict) else overlay_config())["config"]
    cfg["layout"] = "free"
    write_json(OVERLAY_CONFIG_JSON, cfg)
    write_json(OVERLAY_EDIT_REQUEST_JSON, {"open": True, "at": time.time()})
    ensure_overlay_process()
    return {"ok": True, "config": cfg}


def overlay_runtime():
    d = read_json(OVERLAY_RUNTIME_JSON, {})
    pid = None
    try: pid = int(OVERLAY_PID_FILE.read_text().strip())
    except Exception: pass
    alive = False
    if pid:
        try: os.kill(pid, 0); alive = True
        except Exception: pass
    d["running"] = alive
    return d


def ensure_overlay_process():
    try:
        pid = int(OVERLAY_PID_FILE.read_text().strip()) if OVERLAY_PID_FILE.exists() else None
        if pid:
            try:
                os.kill(pid, 0)
                return True
            except Exception:
                OVERLAY_PID_FILE.unlink(missing_ok=True)
        if not OVERLAY_SCRIPT.is_file(): return False
        p = subprocess.Popen([sys.executable, str(OVERLAY_SCRIPT)], cwd=str(BASE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        OVERLAY_PID_FILE.write_text(str(p.pid))
        return True
    except Exception as e:
        log(f"Overlay start failed: {e}")
        return False


def steam_player_count():
    now = time.time()
    if now - _PLAYER_COUNT_CACHE.get("at", 0) < 20:
        return {"ok": _PLAYER_COUNT_CACHE.get("value") is not None, "count": _PLAYER_COUNT_CACHE.get("value"), "scope": "steam"}
    value = None
    try:
        url = f"https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={STEAM_APP_ID}"
        req = Request(url, headers={"User-Agent": f"PG3D-Mac-Client/{APP_VERSION}"})
        with urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        value = int(data.get("response", {}).get("player_count"))
    except Exception as e:
        log(f"Steam player count failed: {e}")
    _PLAYER_COUNT_CACHE["value"] = value
    _PLAYER_COUNT_CACHE["at"] = now
    return {"ok": value is not None, "count": value, "scope": "steam"}

def prefs_payload():
    prefs = read_json(PREFS_JSON, {})
    if "fps_target" not in prefs:
        prefs["fps_target"] = "uncapped"
    snd = dict({"enabled": True, "rankup": True, "volume": 70})
    snd.update(prefs.get("sound") or {})
    prefs["sound"] = snd
    return prefs


UI_THEMES = ("ember", "crimson", "dark_souls", "royal", "icy", "cute")
SOUND_DEFAULTS = {"enabled": True, "rankup": True, "volume": 70}


def sound_prefs():
    snd = dict(SOUND_DEFAULTS)
    snd.update(prefs_payload().get("sound") or {})
    return snd


UI_SOUND_SLOTS = ("click", "primary", "on", "off", "tab", "tick")
CUSTOM_SOUNDS_DIR = SUPPORT / "sounds" / "custom"


def _sound_ids():
    try:
        ids = {x["id"] for x in json.loads((WEB / "sounds/catalog.json").read_text(encoding="utf-8"))["sounds"]}
    except Exception:
        ids = set()
    return ids | {x["id"] for x in custom_sounds()}


def _clean_picks(raw):
    ids, out = _sound_ids(), {}
    for style in ("modern", "noble"):
        picks = (raw or {}).get(style) if isinstance(raw, dict) else None
        if isinstance(picks, dict):
            out[style] = {k: str(v) for k, v in picks.items() if k in UI_SOUND_SLOTS and str(v) in ids}
    return out


def custom_sounds():
    data = read_json(CUSTOM_SOUNDS_DIR / "library.json", {"sounds": []})
    return [x for x in data.get("sounds", []) if (CUSTOM_SOUNDS_DIR / x.get("file", "")).is_file()]


def add_custom_sound(data_url, name):
    if "," not in str(data_url):
        raise RuntimeError("Invalid sound upload.")
    raw = base64.b64decode(str(data_url).split(",", 1)[1])
    if len(raw) > 3 * 1024 * 1024:
        raise RuntimeError("Button sounds must be smaller than 3 MB.")
    ext = next((e for sig, e in _AUDIO_SIGS if raw.startswith(sig)), None) or (".m4a" if raw[4:8] == b"ftyp" else None)
    if not ext:
        raise RuntimeError("Use an MP3, WAV, M4A, OGG or FLAC file.")
    CUSTOM_SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    sid = "c_" + hashlib.sha256(raw).hexdigest()[:12]
    (CUSTOM_SOUNDS_DIR / f"{sid}{ext}").write_bytes(raw)
    lib = read_json(CUSTOM_SOUNDS_DIR / "library.json", {"sounds": []})
    title = re.sub(r"\.[a-z0-9]{2,4}$", "", Path(str(name or "Sound")).name, flags=re.I)[:40] or "Sound"
    lib["sounds"] = [x for x in lib.get("sounds", []) if x.get("id") != sid] + [{"id": sid, "name": title, "file": f"{sid}{ext}"}]
    write_json(CUSTOM_SOUNDS_DIR / "library.json", lib)
    return {"ok": True, "id": sid, "sounds": custom_sounds()}


def remove_custom_sound(sid):
    lib = read_json(CUSTOM_SOUNDS_DIR / "library.json", {"sounds": []})
    for x in lib.get("sounds", []):
        if x.get("id") == sid:
            (CUSTOM_SOUNDS_DIR / Path(x["file"]).name).unlink(missing_ok=True)
    lib["sounds"] = [x for x in lib.get("sounds", []) if x.get("id") != sid]
    write_json(CUSTOM_SOUNDS_DIR / "library.json", lib)
    return {"ok": True, "sounds": custom_sounds()}


def set_sound_prefs(raw):
    snd = sound_prefs()
    for k in ("enabled", "rankup"):
        if k in raw:
            snd[k] = bool(raw[k])
    if "picks" in raw:
        snd["picks"] = _clean_picks(raw.get("picks"))
    if "volume" in raw:
        snd["volume"] = max(1, min(100, int(raw.get("volume") or 70)))
    prefs = prefs_payload()
    prefs["sound"] = snd
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "sound": snd}


SOUNDS_DIR = SUPPORT / "sounds"
_AUDIO_SIGS = ((b"ID3", ".mp3"), (b"\xff\xfb", ".mp3"), (b"\xff\xf3", ".mp3"), (b"\xff\xf2", ".mp3"),
               (b"RIFF", ".wav"), (b"OggS", ".ogg"), (b"fLaC", ".flac"))


def _rankup_file():
    return next(iter(sorted(SOUNDS_DIR.glob("rankup.*"))), None) if SOUNDS_DIR.exists() else None


def set_rankup_sound(data_url, name):
    if "," not in str(data_url):
        raise RuntimeError("Invalid sound upload.")
    raw = base64.b64decode(str(data_url).split(",", 1)[1])
    if len(raw) > 10 * 1024 * 1024:
        raise RuntimeError("Sound files must be smaller than 10 MB.")
    ext = next((e for sig, e in _AUDIO_SIGS if raw.startswith(sig)), None)
    if ext is None and raw[4:8] == b"ftyp":
        ext = ".m4a"
    if ext is None:
        raise RuntimeError("Use an MP3, WAV, M4A, OGG or FLAC file.")
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    for old in SOUNDS_DIR.glob("rankup.*"):
        old.unlink(missing_ok=True)
    (SOUNDS_DIR / f"rankup{ext}").write_bytes(raw)
    snd = sound_prefs()
    snd["custom_rankup"] = hashlib.sha256(raw).hexdigest()[:12]
    snd["custom_rankup_name"] = Path(str(name or "sound")).name[:60]
    prefs = prefs_payload()
    prefs["sound"] = snd
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "sound": snd}


def reset_rankup_sound():
    for old in SOUNDS_DIR.glob("rankup.*") if SOUNDS_DIR.exists() else []:
        old.unlink(missing_ok=True)
    snd = sound_prefs()
    snd.pop("custom_rankup", None)
    snd.pop("custom_rankup_name", None)
    prefs = prefs_payload()
    prefs["sound"] = snd
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "sound": snd}


def clip_sound_volume():
    snd = sound_prefs()
    return snd["volume"] / 100.0 if snd["enabled"] else 0.0


def set_ui_theme_pref(value):
    if value not in UI_THEMES:
        raise RuntimeError("Unknown appearance preset.")
    prefs = prefs_payload()
    prefs["ui_theme"] = value
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "ui_theme": value}


def set_ui_style_pref(raw):
    prefs = prefs_payload()
    style = raw.get("style")
    if style is not None:
        if style not in ("modern", "noble"):
            raise RuntimeError("Unknown style.")
        prefs["ui_style"] = style
    if "animations" in raw:
        prefs["animations"] = bool(raw["animations"])
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "ui_style": prefs.get("ui_style", "modern"), "animations": prefs.get("animations", True)}


def set_fps_pref(value):
    val = fps_target_value(value)
    human = "uncapped" if val == "-1" else val
    prefs = prefs_payload()
    prefs["fps_target"] = human
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "fps_target": human}


def status_payload():
    with _LOCK:
        s = copy.deepcopy(STATUS)
    app = find_app()
    target = s.get("cursor_target") or load_cursor_target()
    if target:
        s["cursor_target"] = target
    gui = gui_catalog()
    mods = load_mods()
    hist = read_json(HISTORY_JSON, default_history())
    version = build = "unknown"
    if app:
        version = plist_value(app / "Contents/Info.plist", "CFBundleShortVersionString")
        build = plist_value(app / "Contents/Info.plist", "CFBundleVersion")
    try:
        account_info = steam_accounts()
    except Exception as e:
        log("Steam account status read failed without blocking UI: " + repr(e))
        account_info = {
            "accounts": [],
            "selected": None,
            "current": None,
            "error": "Steam account data could not be read. Play and Cursor remain available."
        }

    with _LOCK:
        pending_guard = copy.deepcopy(PENDING_GUARD)
    if pending_guard:
        s['pending_guard'] = {
            'steamid64': pending_guard.get('steamid64'),
            'launch_kind': pending_guard.get('launch_kind'),
            'created_at': pending_guard.get('created_at'),
        }
    else:
        s['pending_guard'] = None

    s.update({
        "version": APP_VERSION,
        "game_running": game_running(),
        "steam_running": steam_running(),
        "app_found": app is not None,
        "app_path": str(app) if app else None,
        "game_version": version,
        "game_build": build,
        "custom_cursor": CUSTOM_CURSOR.exists(),
        "cursor_catalog": CURSOR_CATALOG,
        "mods": mods,
        "history": {"undo": len(hist.get("undo", [])), "redo": len(hist.get("redo", [])),
                    "undo_label": hist.get("undo", [{}])[-1].get("label") if hist.get("undo") else None,
                    "redo_label": hist.get("redo", [{}])[-1].get("label") if hist.get("redo") else None},
        "gui_scan": {"generated": gui.get("generated"), "count": len(gui.get("items", [])), "deep": gui.get("deep", False)},
        "accounts": account_info,
        "prefs": prefs_payload(),
        "fps_bundle_present": fps_bundle_present(),
        "fps_supported_builds": sorted(FPS_SUPPORTED_BUILDS),
        "fps_build_match": fps_build_match(build),
        "game_stuck": game_stuck(),
        "clips": {k: v for k, v in clips.RECORDER.status().items() if k in ("phase", "saving", "buffered", "last_clip")},
        "update": ({k: v for k, v in updater.UPDATER.state().items() if k in ("available", "phase", "message")}
                   if updater.UPDATER else None),
        "playtime": playtime_summary(account_info.get("current") or account_info.get("selected") or "unknown"),
        "rank": _safe_rank(account_info.get("current") or account_info.get("selected") or "unknown"),
        "last_session": last_session_payload(),
        "game_update": game_update_info(),
        "reapply_mods": reapply_enabled(),
        "mouse_fix": mouse_fix_enabled(),
        "job": {k[4:]: s.get(k) for k in ("job_kind", "job_id", "job_phase", "job_progress", "job_message", "job_error",
                                           "job_eta", "job_queue")},
        "overlay": {"config": overlay_config(), "runtime": overlay_runtime()},
    })
    return s


def _safe_rank(account):
    try:
        r = rank_payload(account)
        return {k: r.get(k) for k in ("title", "tier", "tier_name", "division", "color", "xp", "progress",
                                      "next_name", "xp_to_next", "hours_to_next", "emblem", "last_event",
                                      "wins", "hours", "gated", "lp", "pending")}
    except Exception as e:
        log(f"Rank summary failed: {e}")
        return None


def _fmt_duration(seconds):
    seconds = int(max(0, seconds))
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _playtime_account():
    try:
        info = steam_accounts()
        return info.get("current") or info.get("selected") or "unknown"
    except Exception:
        return "unknown"


def _rank_snapshot(account):
    try:
        r = rank_payload(account)
        ember = (wins_data()["accounts"].get(str(account), {}).get("ember") or {})
        return {"step": r["step"], "tier": r["tier"], "name": r["tier_name"], "xp": r["xp"], "wins": r["wins"],
                "progress": round(float(r["progress"]), 4), "next": r.get("next_name"), "color": r["color"],
                "player": ember.get("player_id")}
    except Exception as e:
        log(f"Rank snapshot failed: {e}")
        return None


def write_overlay_rank(account):
    snap = _rank_snapshot(account)
    if snap:
        try:
            write_json(OVERLAY_RANK_JSON, snap)
        except Exception:
            pass
    return snap


def _clips_since(start):
    try:
        return sum(1 for p in clips.CLIPS_DIR.glob("*.mp4") if p.stat().st_mtime >= start)
    except Exception:
        return 0


def _finish_session_report(session, length):
    after = _rank_snapshot(session["account"]) or {}
    before = session.get("rank") or {}
    if before.get("player") != after.get("player"):
        before = {}  # numbers from two different PG3D players can't be compared
    report = {
        "account": session["account"], "start": int(session["start"]), "seconds": int(length),
        "wins": max(0, int(after.get("wins", 0)) - int(before.get("wins", after.get("wins", 0)))),
        "xp": max(0, int(after.get("xp", 0)) - int(before.get("xp", after.get("xp", 0)))),
        "clips": _clips_since(session["start"]),
        "before": before or None, "after": after or None,
        "promoted": bool(before and after and after.get("step", 0) > before.get("step", 0)),
    }
    write_json(LAST_SESSION_JSON, report)
    log(f"Session report: {_fmt_duration(length)}, +{report['xp']} XP, {report['wins']} wins")


def last_session_payload():
    rep = read_json(LAST_SESSION_JSON, None)
    if not isinstance(rep, dict) or not rep.get("seconds"):
        return None
    if int(rep.get("wins", 0)) > rep["seconds"] / 60 + 2:
        return None  # impossible (a wrong-account mix-up from before 1.46)
    return {**rep, "duration": _fmt_duration(rep["seconds"])}


def playtime_watch():
    """Record PG3D sessions per Steam account while the client is open."""
    session = None
    last_rank_write = 0.0
    while True:
        try:
            running = _game_running_uncached()
            now = time.time()
            if running and session is None:
                account = _playtime_account()
                session = {"account": account, "start": now, "rank": _rank_snapshot(account)}
                with _LOCK:
                    STATUS["session_start"] = now
            elif not running and session is not None:
                length = now - session["start"]
                if length < 45 and not _LAUNCH.get("stage"):
                    # PG3D closed right after opening: often damaged game files.
                    log(f"PG3D closed {length:.0f}s after starting (mods active: {mods_active()})")
                    set_status(quick_exit={"at": int(now), "seconds": int(length), "mods": mods_active()})
                elif length >= 45:
                    set_status(quick_exit=None)
                with _LOCK:
                    STATUS["session_start"] = None
                if length >= 30:
                    data = read_json(PLAYTIME_JSON, {"sessions": []})
                    sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
                    sessions.append({"account": session["account"], "start": int(session["start"]), "seconds": int(length)})
                    data["sessions"] = sessions[-2000:]
                    write_json(PLAYTIME_JSON, data)
                    try:
                        _finish_session_report(session, length)
                    except Exception as e:
                        log(f"Session report failed: {e}")
                session = None
            if running and now - last_rank_write >= 30:
                last_rank_write = now
                snap = write_overlay_rank(session["account"] if session else _playtime_account())
                if session and snap and (session.get("rank") or {}).get("player") != snap.get("player"):
                    session["rank"] = snap  # linked to another PG3D ID mid-session: count from here
        except Exception as e:
            log(f"Playtime tracker error: {e}")
        time.sleep(5)


def playtime_summary(account):
    data = read_json(PLAYTIME_JSON, {"sessions": []})
    sessions = [x for x in data.get("sessions", []) if isinstance(x, dict) and x.get("account") == account]
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    week_ago = time.time() - 7 * 86400
    today = sum(x.get("seconds", 0) for x in sessions if x.get("start", 0) >= midnight)
    week = sum(x.get("seconds", 0) for x in sessions if x.get("start", 0) >= week_ago)
    total = sum(x.get("seconds", 0) for x in sessions)
    with _LOCK:
        live_start = STATUS.get("session_start")
    live = time.time() - live_start if live_start else 0
    return {
        "today": _fmt_duration(today + live), "week": _fmt_duration(week + live),
        "total": _fmt_duration(total + live), "sessions": len(sessions),
        "live": _fmt_duration(live) if live_start else None,
    }


def _dir_size(path: Path):
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file() and not p.is_symlink():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


CACHE_DIRS = ("clip_thumbs", "fps_logs", "music_cache", "ember_icons")


def settings_payload():
    def storage():
        clips_list = clips.list_clips()
        return {
            "clips_count": len(clips_list),
            "clips_bytes": sum(c["size"] for c in clips_list),
            "support_bytes": _dir_size(SUPPORT),
            "cache_bytes": sum(_dir_size(SUPPORT / d) for d in CACHE_DIRS),
        }
    app_bundle = os.environ.get("PG3D_APP_BUNDLE") or ""
    ffmpeg = clips.find_ffmpeg()
    st = clips.RECORDER.status()
    upd = updater.UPDATER.state() if updater.UPDATER else None
    return {
        "ok": True,
        "version": APP_VERSION,
        "app_path": app_bundle,
        "installed": app_bundle.startswith("/Applications/") or app_bundle.startswith(str(Path.home() / "Applications")),
        "update": upd,
        "setup": {
            "python": sys.version.split()[0],
            "ffmpeg": ffmpeg,
            "ffmpeg_install": st.get("install"),
            "screen_permission": st.get("permission"),
            "downloads_access": not (upd and upd.get("phase") == "no_access"),
        },
        "storage": cached("settings_storage", 20, storage),
        "clips_folder": str(clips.CLIPS_DIR),
    }


def diagnostics_report():
    def run(args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:
            return "?"
    def tail(path: Path, n):
        try:
            return "\n".join(path.read_text(errors="ignore").splitlines()[-n:])
        except Exception:
            return "(missing)"
    ffmpeg = clips.find_ffmpeg()
    lines = [
        f"PG3D Mac Client {APP_VERSION}",
        f"macOS {run(['/usr/bin/sw_vers', '-productVersion'])} ({run(['/usr/bin/uname', '-m'])})",
        f"App: {os.environ.get('PG3D_APP_BUNDLE')}",
        f"Python: {sys.version.split()[0]} at {sys.executable}",
        f"ffmpeg: {ffmpeg or 'not installed'}",
        f"Game: {find_app()} build {status_payload().get('game_build')}",
        f"Clips: {json.dumps(clips.RECORDER.status(), default=str)}",
        f"Update: {json.dumps(updater.UPDATER.state() if updater.UPDATER else None, default=str)}",
        "", "--- launcher.log ---", tail(SUPPORT / "launcher.log", 25),
        "", "--- client.log ---", tail(LOG, 80),
        "", "--- ffmpeg.log ---", tail(SUPPORT / "ffmpeg.log", 20),
        "", "--- focus.log (front app while PG3D runs) ---", tail(SUPPORT / "focus.log", 40),
        "", f"--- last game run (mouse fix {'on' if mouse_fix_enabled() else 'off'}) ---",
        _game_run_summary(max(FPS_LOG_DIR.glob("run-*.log"), key=lambda p: p.stat().st_mtime, default=SUPPORT / "none.log")),
        "", "--- recent game-file jobs ---", "\n".join(_JOB_HISTORY) or "(none)",
        f"now: {STATUS.get('job_phase')} {STATUS.get('job_message')} queue={STATUS.get('job_queue')}",
        "", "--- applied mods ---", _mods_summary(),
        "", "--- PG3D's own log (Player.log) ---", _unity_log_tail(),
        "", "--- newest PG3D crash report ---", _crash_report_summary(),
    ]
    text = "\n".join(lines)
    try:
        subprocess.run(["/usr/bin/pbcopy"], input=text, text=True, timeout=5)
    except Exception:
        pass
    return {"ok": True, "chars": len(text)}


def _mods_summary():
    m = load_mods()
    out = []
    c = m.get("cursor")
    if c:
        t = c.get("target") or {}
        out.append(f"cursor: {c.get('cursor')} -> {Path(str(t.get('source_file', ''))).name} #{t.get('path_id')} {t.get('name', '')}")
    for key in ("skybox", "loading"):
        v = m.get(key)
        if v:
            names = [f"{x.get('name')} ({Path(str(x.get('source_file', ''))).name})" for x in v.get("targets", [])]
            out.append(f"{key}: {v.get('name')} on {len(names)} textures: " + ", ".join(names[:25]))
    if m.get("font"):
        out.append(f"font: {(m.get('font') or {}).get('name')}")
    out.append(f"gui edits: {len(m.get('gui') or {})}")
    cat = customize_catalog()
    out.append(f"scan: build {cat.get('build')} v{cat.get('scan_v', 1)}, {len(cat.get('sky', []))} sky, "
               f"{len(cat.get('loading', []))} loading screens: "
               + ", ".join(x.get("name", "") for x in cat.get("loading", [])[:30]))
    return "\n".join(out)


def _unity_log_tail(lines=40):
    logs = sorted(Path.home().glob("Library/Logs/*/*/Player*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    logs = [p for p in logs if "pixel" in str(p).lower()]
    if not logs:
        return "(missing)"
    p = logs[0]
    try:
        text = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(unreadable)"
    stamp = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    keep = [l for l in text if l.strip() and not l.startswith(("    \"memorysetup", "(Filename:"))]
    return f"{p.name}, last written {stamp}\n" + "\n".join(keep[-lines:])


def _crash_report_summary():
    reps = sorted(Path.home().glob("Library/Logs/DiagnosticReports/Pixel Gun 3D*"), key=lambda p: p.stat().st_mtime,
                  reverse=True)
    if not reps:
        return "(none)"
    p = reps[0]
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return p.name
    picks = []
    for key in ("exception", "termination", "faultingThread", "Exception Type", "Termination Reason",
                "Crashed Thread", "dyld", "Library not loaded", "Reason"):
        for m in re.finditer(r'.{0,60}' + re.escape(key) + r'.{0,160}', text):
            picks.append(m.group(0).strip())
            break
    stamp = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    return f"{p.name} ({stamp})\n" + "\n".join(picks[:10])


def _game_run_summary(path):
    """The game-run log without its 5-second FPS samples drowning everything else."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(missing)"
    samples = [l for l in text if "engine measurement:" in l or "presentation sample:" in l or "presentation screen:" in l]
    events = [l for l in text if l not in samples]
    focused = sum(1 for l in samples if "focused=yes" in l)
    unfocused = sum(1 for l in samples if "focused=no" in l)
    out = events[-40:]
    if samples:
        out += [f"(FPS samples: {focused} with PG3D focused, {unfocused} in the background; last ones:)"] + samples[-3:]
    return "\n".join(out) or "(empty)"


def refocus_game():
    """Bring PG3D back to the front so it re-locks the mouse (the client is in front when this runs)."""
    app = find_app()
    if not app or not game_running():
        raise RuntimeError("Pixel Gun 3D isn't running.")
    subprocess.Popen(["/usr/bin/open", str(app)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}


def clear_cache():
    for d in CACHE_DIRS:
        shutil.rmtree(SUPPORT / d, ignore_errors=True)
        (SUPPORT / d).mkdir(parents=True, exist_ok=True)
    invalidate("settings_storage")
    return {"ok": True}


def reveal(what):
    target = {"logs": LOG, "support": SUPPORT}.get(what)
    if what == "clips":
        return clips.reveal_clip()
    if what == "app" and os.environ.get("PG3D_APP_BUNDLE"):
        target = Path(os.environ["PG3D_APP_BUNDLE"])
    if not target:
        raise RuntimeError("Unknown location.")
    subprocess.Popen(["/usr/bin/open", "-R", str(target)] if target.is_file() or str(target).endswith(".app")
                     else ["/usr/bin/open", str(target)])
    return {"ok": True}


# ------------------------------------------------------------ background jobs
def _sub_progress(progress, lo, hi):
    return lambda frac, msg: progress(lo + (hi - lo) * max(0.0, min(1.0, frac)), msg)


JOB_LABELS = {"sky": "Sky", "theme": "Theme", "font": "Font", "scan": "Scanning game files", "change": "Change",
              "reapply": "Re-applying mods", "restore": "Restoring game files", "repair": "Fixing textures"}
_JOBS = {"queue": [], "seq": 0, "worker": None, "current": None}
_JOB_COND = threading.Condition()
_JOB_HISTORY = []  # recent results, for the diagnostic report


def jobs_pending():
    with _JOB_COND:
        return bool(_JOBS["queue"]) or _JOBS["current"] is not None


def _queue_status():
    with _JOB_COND:
        q = [t["label"] for t in _JOBS["queue"]]
    set_status(job_queue=q)


def start_job(kind, fn, label=None):
    """Queue a game-file task. Tasks run one after another; each waits by itself
    while PG3D is open or starting, so clicking Apply never fails with "busy"."""
    with _JOB_COND:
        _JOBS["seq"] += 1
        task = {"id": _JOBS["seq"], "kind": kind, "label": label or JOB_LABELS.get(kind, kind.title()), "fn": fn}
        _JOBS["queue"].append(task)
        ahead = len(_JOBS["queue"]) - 1 + (1 if _JOBS["current"] else 0)
        if _JOBS["worker"] is None or not _JOBS["worker"].is_alive():
            _JOBS["worker"] = threading.Thread(target=_job_worker, daemon=True)
            _JOBS["worker"].start()
        _JOB_COND.notify_all()
    _queue_status()
    queued = ahead > 0 or game_running()
    if queued:
        _REQ.queued = True
    return {"ok": True, "queued": queued, "ahead": ahead}


def _job_progress_fn(task):
    started = time.time()
    hist = {"frac": 0.0}

    def progress(frac, msg):
        frac = max(0.0, min(1.0, frac))
        el = time.time() - started
        eta = None
        # Remaining time from the real pace so far, once there's enough to go on.
        if frac >= 0.04 and el >= 2:
            eta = int(round(el * (1 - frac) / frac))
        hist["frac"] = frac
        set_status(job_progress=int(round(frac * 100)), job_message=msg, job_eta=eta)
    return progress


def _job_worker():
    _JOBS["thread_ident"] = threading.get_ident()
    while True:
        with _JOB_COND:
            while not _JOBS["queue"]:
                _JOB_COND.wait(timeout=30)
            task = _JOBS["queue"].pop(0)
            _JOBS["current"] = task
        _queue_status()
        # Game files can only change while PG3D is closed: wait instead of failing.
        while game_running() or (launch_busy() and not _LAUNCH.get("waiting_jobs")):
            set_status(job_kind=task["kind"], job_phase="waiting", job_progress=0, job_eta=None, job_error=None,
                       job_message=f"{task['label']}: waiting for PG3D to close…")
            time.sleep(1.0)
        set_status(job_kind=task["kind"], job_id=task["id"], job_phase="running", job_progress=0, job_eta=None,
                   job_message=f"{task['label']}…", job_error=None)
        t0 = time.time()
        try:
            msg = task["fn"](_job_progress_fn(task)) or "Done."
            set_status(job_phase="done", job_progress=100, job_eta=0, job_message=msg)
            log(f"Job {task['kind']} finished: {msg}")
            _JOB_HISTORY.append(f"{datetime.now():%H:%M:%S} {task['label']}: done in {time.time() - t0:.0f}s ({msg})")
        except Exception as e:
            log(traceback.format_exc())
            set_status(job_phase="error", job_error=str(e), job_message="Failed.", job_eta=None)
            _JOB_HISTORY.append(f"{datetime.now():%H:%M:%S} {task['label']}: FAILED after {time.time() - t0:.0f}s: {e}")
        finally:
            del _JOB_HISTORY[:-15]
            with _JOB_COND:
                _JOBS["current"] = None
            _queue_status()


def in_job_worker():
    return threading.get_ident() == _JOBS.get("thread_ident")


# ------------------------------------------------------- theme + sky catalog
THEME_SAMPLES = RUNTIME / "theme_samples"


# 2: objects from files UnityPy pulled in while scanning another file are no
# longer recorded under the wrong file.
# 3: loading screens / skies matched by whole words and real sky shaders (weapon
# "Reloading" animations and "SkyHammer" textures were picked up before).
# 4: loading-screen candidates (map previews etc.) listed, ticked only when named so.
CUSTOMIZE_SCAN_V = 4


def customize_catalog():
    return read_json(CUSTOMIZE_CATALOG_JSON, {"generated": None, "app": None, "build": None, "gui": {}, "sky": [], "loading": []})


def _catalog_fresh(cat):
    app = find_app()
    if not app or not cat.get("generated") or cat.get("app") != str(app) or "loading" not in cat or "fonts" not in cat:
        return False
    if cat.get("scan_v", 1) < CUSTOMIZE_SCAN_V:
        return False
    build = plist_value(app / "Contents/Info.plist", "CFBundleVersion")
    return cat.get("build") == build


def scan_customize(progress):
    app = find_app()
    if not app:
        raise RuntimeError("Pixel Gun 3D.app was not found in your Steam libraries.")
    root = app / "Contents/Resources/Data"
    files = candidate_files(root, deep=True)
    if not files:
        raise RuntimeError("No readable Unity asset files were found in the PG3D app.")
    shutil.rmtree(THEME_SAMPLES, ignore_errors=True)
    THEME_SAMPLES.mkdir(parents=True, exist_ok=True)
    for pat in ("target_*.jpg", "loading_*.jpg"):
        for old in SKY_PREVIEWS.glob(pat):
            old.unlink(missing_ok=True)
    samples = []

    def keep_sample(img, meta):
        name = f"{len(samples) + 1:02d}.png"
        img.save(THEME_SAMPLES / name)
        samples.append({"file": name, "name": meta.get("name", ""), "width": meta["width"], "height": meta["height"]})

    loading, fonts = [], []
    gui, sky = customize.scan_assets(files, app, GUI_HINTS, progress, log, sample_cb=keep_sample,
                                     loading=loading, fonts=fonts)
    for t in loading:
        thumb = t.pop("_thumb", None)
        if thumb is not None:
            try:
                thumb.save(SKY_PREVIEWS / f"loading_{t['id']}.jpg", quality=85)
                t["preview"] = f"/api/sky/loading/{t['id']}.jpg"
            except Exception:
                pass
    for t in sky:
        thumb = t.pop("_thumb", None)
        if thumb is not None:
            try:
                thumb.save(SKY_PREVIEWS / f"target_{t['id']}.jpg", quality=85)
                t["preview"] = f"/api/sky/target/{t['id']}.jpg"
            except Exception:
                pass
    data = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "app": str(app),
        "build": plist_value(app / "Contents/Info.plist", "CFBundleVersion"),
        "scan_v": CUSTOMIZE_SCAN_V,
        "gui": gui,
        "sky": sky,
        "loading": loading,
        "fonts": fonts,
        "samples": samples,
        "files_scanned": len(files),
    }
    write_json(CUSTOMIZE_CATALOG_JSON, data)
    log(f"Customize scan: {sum(len(v) for v in gui.values())} GUI textures in {len(gui)} files, "
        f"{len(sky)} sky textures, {len(loading)} loading screens, {len(fonts)} fonts")
    return data


def ensure_catalog(progress, force=False):
    cat = customize_catalog()
    if not force and _catalog_fresh(cat):
        progress(1.0, "Using the saved scan.")
        return cat
    return scan_customize(progress)


def theme_payload():
    cat = customize_catalog()
    mods = load_mods()
    theme = mods.get("theme")
    return {
        "ok": True,
        "presets": [{"id": k, "name": v["name"], "desc": v["desc"], "stops": v["stops"], "strength": v.get("strength")}
                    for k, v in customize.THEMES.items()],
        "active": ({"preset": theme["preset"], "strength": theme.get("strength", 0.85),
                    "textures": sum(len(v) for v in theme.get("targets", {}).values()),
                    "files": len(theme.get("targets", {}))} if theme else None),
        "scan": {"generated": cat.get("generated"), "fresh": _catalog_fresh(cat),
                 "textures": sum(len(v) for v in cat.get("gui", {}).values()),
                 "files": len(cat.get("gui", {})), "files_scanned": cat.get("files_scanned")},
        "samples": [f"/api/theme/sample/{s['file']}" for s in cat.get("samples", [])],
    }


def theme_targets(cat):
    # Sky textures are never themed: a custom sky keeps its picture, the stock sky its colors.
    sky_ids = {(t["source_file"], int(t["path_id"])) for t in cat.get("sky", [])}
    targets = {}
    for src, items in cat.get("gui", {}).items():
        if not Path(src).exists():
            continue
        ids = sorted({int(t["path_id"]) for t in items} - {pid for (f, pid) in sky_ids if f == src})
        if ids:
            targets[src] = ids
    return targets


def apply_theme(preset, strength=None):
    if preset not in customize.THEMES:
        raise RuntimeError("Unknown theme preset.")
    # Each preset has its own tuned strength; there is no user-facing intensity.
    strength = float(customize.THEMES[preset].get("strength", 0.8))
    name = customize.THEMES[preset]["name"]

    def job(progress):
        cat = ensure_catalog(_sub_progress(progress, 0, 0.4))
        targets = theme_targets(cat)
        if not targets:
            raise RuntimeError("No GUI textures were found to recolour.")

        def change(state):
            state["theme"] = {"preset": preset, "strength": strength, "targets": targets}
        mutate_state(change, f"Theme: {name}", _sub_progress(progress, 0.4, 1.0))
        count = sum(len(v) for v in targets.values())
        return f"{name} applied to {count} GUI textures in {len(targets)} files."
    return start_job("theme", job)


def reset_theme():
    def job(progress):
        def change(state):
            state["theme"] = None
        mutate_state(change, "Reset theme", progress)
        return "Original PG3D colors restored."
    return start_job("theme", job)


# Steam store screenshot of the Armory: almost entirely menus and item icons,
# i.e. exactly what a theme recolors. Fetched once, then cached.
THEME_SHOT_URLS = [
    "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/2524890/ss_9513357c02bd274b31bbcfa00c7b5c8b9ca652ea.1920x1080.jpg",
]
THEME_SHOT_BASE = SUPPORT / "theme_preview_base.jpg"
THEME_SHOT_DIR = RUNTIME / "theme_shots"


_THEME_SHOT_LOCK = threading.Lock()


def _theme_shot_base():
    with _THEME_SHOT_LOCK:
        return _theme_shot_base_locked()


def _theme_shot_base_locked():
    if THEME_SHOT_BASE.is_file():
        return THEME_SHOT_BASE
    urls = list(THEME_SHOT_URLS)
    try:
        req = Request(f"https://store.steampowered.com/api/appdetails?appids={STEAM_APP_ID}&filters=screenshots",
                      headers={"User-Agent": f"PG3D-Mac-Client/{APP_VERSION}"})
        with urlopen(req, timeout=8) as r:
            shots = json.loads(r.read().decode("utf-8"))[STEAM_APP_ID]["data"]["screenshots"]
        urls += [x["path_full"] for x in shots]
    except Exception as e:
        log(f"Theme preview screenshot list failed: {e}")
    for url in urls:
        try:
            with urlopen(Request(url, headers={"User-Agent": f"PG3D-Mac-Client/{APP_VERSION}"}), timeout=12) as r:
                data = r.read(8 * 1024 * 1024)
            im = Image.open(io.BytesIO(data)).convert("RGB")
            im.thumbnail((1280, 720))
            im.save(THEME_SHOT_BASE, quality=90)
            return THEME_SHOT_BASE
        except Exception as e:
            log(f"Theme preview screenshot failed ({url[:60]}…): {e}")
    return None


def render_theme_shot(preset):
    base = _theme_shot_base()
    if not base:
        raise RuntimeError("No preview screenshot available (offline?).")
    THEME_SHOT_DIR.mkdir(parents=True, exist_ok=True)
    key = preset if preset in customize.THEMES else "original"
    out = THEME_SHOT_DIR / f"{key}-v2.jpg"  # v2 = palette remap
    with _THEME_SHOT_LOCK:
        if not out.is_file() or out.stat().st_mtime < base.stat().st_mtime:
            im = Image.open(base).convert("RGBA")
            if key != "original":
                im = customize.recolor_image(im, key, customize.THEMES[key].get("strength", 1.0))
            tmp = out.with_suffix(".tmp.jpg")
            im.convert("RGB").save(tmp, quality=88)
            os.replace(tmp, out)
    return out


def render_theme_sample(index, preset, strength):
    cat = customize_catalog()
    samples = cat.get("samples", [])
    if not samples:
        raise RuntimeError("Scan the game first.")
    item = samples[max(0, min(len(samples) - 1, int(index)))]
    img = Image.open(THEME_SAMPLES / item["file"]).convert("RGBA")
    if preset in customize.THEMES:
        img = customize.recolor_image(img, preset, float(strength))
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


# ------------------------------------------------------------------- skyboxes
SKY_LIBRARY_JSON = SKY_DIR / "library.json"


def sky_library():
    data = read_json(SKY_LIBRARY_JSON, {"skies": []})
    skies = [s for s in data.get("skies", []) if (SKY_DIR / s.get("file", "")).is_file()]
    return skies


def _sky_by_id(sky_id):
    for s in sky_library():
        if s["id"] == sky_id:
            return s
    raise RuntimeError("That sky image is no longer in your library.")


def upload_sky(data_url, name):
    if "," not in str(data_url):
        raise RuntimeError("Invalid image upload.")
    raw = base64.b64decode(str(data_url).split(",", 1)[1])
    if len(raw) > 80 * 1024 * 1024:
        raise RuntimeError("Sky images must be smaller than 80 MB.")
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception:
        raise RuntimeError("That file isn't an image PG3D Client can read (use PNG or JPEG).")
    if im.width > 16384 or im.height > 16384 or im.width < 64 or im.height < 64:
        raise RuntimeError("Sky images must be between 64 and 16384 pixels on each side.")
    sky_id = hashlib.sha256(raw).hexdigest()[:16]
    ext = ".jpg" if (im.format or "").upper() == "JPEG" else ".png"
    path = SKY_DIR / f"{sky_id}{ext}"
    if ext == ".png" and (im.format or "").upper() != "PNG":
        im.convert("RGBA").save(path, "PNG")
    else:
        path.write_bytes(raw)
    thumb = im.convert("RGB")
    thumb.thumbnail((640, 640))
    thumb.save(SKY_PREVIEWS / f"{sky_id}.jpg", quality=85)
    data = read_json(SKY_LIBRARY_JSON, {"skies": []})
    skies = [s for s in data.get("skies", []) if s.get("id") != sky_id]
    skies.insert(0, {
        "id": sky_id, "file": path.name, "name": customize.safe_sky_name(name),
        "width": im.width, "height": im.height,
        "panorama": 1.7 <= im.width / max(1, im.height) <= 2.3,
        "added": datetime.now().isoformat(timespec="seconds"),
    })
    data["skies"] = skies
    write_json(SKY_LIBRARY_JSON, data)
    return {"ok": True, "id": sky_id}


def delete_sky(sky_id):
    mods = load_mods()
    sky = _sky_by_id(sky_id)
    if (mods.get("skybox") or {}).get("image", "") == sky["file"]:
        raise RuntimeError("That sky is in use. Restore the original sky first.")
    if (mods.get("loading") or {}).get("image", "") == sky["file"]:
        raise RuntimeError("That image is your loading screen. Restore the original loading screen first.")
    (SKY_DIR / sky["file"]).unlink(missing_ok=True)
    (SKY_PREVIEWS / f"{sky_id}.jpg").unlink(missing_ok=True)
    data = read_json(SKY_LIBRARY_JSON, {"skies": []})
    data["skies"] = [s for s in data.get("skies", []) if s.get("id") != sky_id]
    write_json(SKY_LIBRARY_JSON, data)
    return {"ok": True}


def sky_payload():
    cat = customize_catalog()
    sky = load_mods().get("skybox")
    return {
        "ok": True,
        "library": [{**s, "thumb": f"/api/sky/thumb/{s['id']}.jpg"} for s in sky_library()],
        "targets": cat.get("sky", []),
        "scan": {"generated": cat.get("generated"), "fresh": _catalog_fresh(cat)},
        "active": ({k: sky.get(k) for k in ("id", "name", "yaw", "swap_lr", "flip")} |
                   {"count": len(sky.get("targets", [])),
                    "target_keys": [f"{t['source_file']}:{t['path_id']}" for t in sky.get("targets", [])]}
                   if sky else None),
        "numpy": customize.np is not None,
        "loading": loading_payload(),
    }


def font_list():
    out = [{**f, "builtin": True} for f in GAME_FONTS if (GAME_FONTS_DIR / f["file"]).is_file()]
    data = read_json(USER_FONTS_DIR / "library.json", {"fonts": []})
    out += [{**f, "builtin": False} for f in data.get("fonts", []) if (USER_FONTS_DIR / f.get("file", "")).is_file()]
    return out


def _font_by_id(font_id):
    for f in font_list():
        if f["id"] == font_id:
            return f
    raise RuntimeError("That font is no longer available.")


def font_path(f):
    return (GAME_FONTS_DIR if f.get("builtin") else USER_FONTS_DIR) / Path(f["file"]).name


def font_bytes(cfg):
    path = (GAME_FONTS_DIR if cfg.get("builtin") else USER_FONTS_DIR) / Path(str(cfg.get("file", ""))).name
    if not path.is_file():
        raise RuntimeError("The selected font file is missing. Pick it again.")
    return path.read_bytes()


def upload_font(data_url, name):
    if "," not in str(data_url):
        raise RuntimeError("Invalid font upload.")
    raw = base64.b64decode(str(data_url).split(",", 1)[1])
    if len(raw) > 30 * 1024 * 1024:
        raise RuntimeError("Fonts must be smaller than 30 MB.")
    if raw[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
        raise RuntimeError("Use a .ttf or .otf font file.")
    USER_FONTS_DIR.mkdir(parents=True, exist_ok=True)
    fid = "u" + hashlib.sha256(raw).hexdigest()[:12]
    ext = ".otf" if raw[:4] == b"OTTO" else ".ttf"
    (USER_FONTS_DIR / f"{fid}{ext}").write_bytes(raw)
    lib = read_json(USER_FONTS_DIR / "library.json", {"fonts": []})
    title = re.sub(r"\.(ttf|otf)$", "", str(name or "Custom font"), flags=re.I).replace("-", " ").replace("_", " ").strip()[:40]
    lib["fonts"] = [f for f in lib.get("fonts", []) if f.get("id") != fid] + [{"id": fid, "name": title or "Custom font", "file": f"{fid}{ext}"}]
    write_json(USER_FONTS_DIR / "library.json", lib)
    return {"ok": True, "id": fid}


def font_payload():
    cat = customize_catalog()
    cfg = load_mods().get("font")
    return {"fonts": [{k: f[k] for k in ("id", "name", "builtin")} for f in font_list()],
            "targets": len(cat.get("fonts", [])) if "fonts" in cat else None,
            "active": cfg.get("id") if cfg else None}


def apply_font(font_id):
    if not font_id:
        def job(progress):
            def change(state):
                state["font"] = None
            mutate_state(change, "Original font", progress)
            return "Original font restored."
        return start_job("font", job)
    f = _font_by_id(font_id)

    def job(progress):
        cat = ensure_catalog(_sub_progress(progress, 0, 0.4))
        picked = [{k: t[k] for k in ("source_file", "relative_file", "path_id", "name")}
                  for t in cat.get("fonts", []) if Path(t["source_file"]).exists()]
        if not picked:
            raise RuntimeError("This PG3D build has no font the client can swap.")

        def change(state):
            state["font"] = {"id": f["id"], "name": f["name"], "file": f["file"], "builtin": f["builtin"], "targets": picked}
        mutate_state(change, f"Font: {f['name']}", _sub_progress(progress, 0.4, 1.0))
        return f"Game text now uses {f['name']}."
    return start_job("font", job)


def loading_payload():
    cat = customize_catalog()
    cfg = load_mods().get("loading")
    return {"targets": cat.get("loading", []),
            "active": ({"id": cfg.get("id"), "name": cfg.get("name"), "count": len(cfg.get("targets", [])),
                        "target_keys": [f"{t['source_file']}:{t['path_id']}" for t in cfg.get("targets", [])]}
                       if cfg else None)}


def _loading_default(targets):
    return [t for t in targets if t.get("suggested", True)]


def apply_loading(sky_id, target_ids=None):
    sky = _sky_by_id(sky_id)

    def job(progress):
        cat = ensure_catalog(_sub_progress(progress, 0, 0.4))
        all_t = cat.get("loading", [])
        chosen = [t for t in all_t if t["id"] in set(target_ids)] if target_ids else _loading_default(all_t)
        picked = [{k: t[k] for k in ("source_file", "relative_file", "path_id", "name", "width", "height")}
                  for t in chosen if Path(t["source_file"]).exists()]
        if not picked:
            raise RuntimeError("This PG3D build has no loading screen texture the client can find.")

        def change(state):
            state["loading"] = {"id": sky["id"], "image": sky["file"], "name": sky["name"], "targets": picked,
                                "targets_v": CUSTOMIZE_SCAN_V}
        mutate_state(change, f"Loading screen: {sky['name']}", _sub_progress(progress, 0.4, 1.0))
        return f"“{sky['name']}” is now the loading screen."
    return start_job("sky", job)


def reset_loading():
    def job(progress):
        def change(state):
            state["loading"] = None
        mutate_state(change, "Reset loading screen", progress)
        return "Original loading screen restored."
    return start_job("sky", job)


def sky_cross_preview(sky_id, yaw=0.0, swap_lr=False, size=160):
    """Unfolded cube (a 4x3 cross) so the user sees how a panorama will wrap."""
    sky = _sky_by_id(sky_id)
    src = Image.open(SKY_DIR / sky["file"]).convert("RGB")
    if max(src.size) > 2048:
        src.thumbnail((2048, 2048))
    pano = customize.as_panorama(src, height=max(256, size * 2))
    if customize.is_panorama(src) and pano.height > size * 4:
        pano = pano.resize((size * 8, size * 4), Image.Resampling.BILINEAR)
    cross = Image.new("RGBA", (size * 4, size * 3), (0, 0, 0, 0))
    layout = {"up": (1, 0), "left": (0, 1), "front": (1, 1), "right": (2, 1), "back": (3, 1), "down": (1, 2)}
    for face, (cx, cy) in layout.items():
        f = face
        if swap_lr and face in ("left", "right"):
            f = "right" if face == "left" else "left"
        cross.paste(customize.project_face(pano, f, size, yaw), (cx * size, cy * size))
    bio = io.BytesIO()
    cross.save(bio, format="PNG")
    return bio.getvalue()


def apply_sky(sky_id, target_ids=None, yaw=0, swap_lr=False, flip=False):
    sky = _sky_by_id(sky_id)
    try:
        yaw = float(yaw) % 360
    except Exception:
        yaw = 0.0

    def job(progress):
        cat = ensure_catalog(_sub_progress(progress, 0, 0.4))
        all_targets = cat.get("sky", [])
        if not all_targets:
            raise RuntimeError("No skybox textures were found in this PG3D build.")
        picked = all_targets if not target_ids else [t for t in all_targets if t["id"] in set(target_ids)]
        picked = [{k: t[k] for k in ("source_file", "relative_file", "path_id", "name", "type", "face", "width", "height")}
                  for t in picked if Path(t["source_file"]).exists()]
        if not picked:
            raise RuntimeError("None of the selected sky textures exist anymore. Scan again.")

        def change(state):
            state["skybox"] = {"id": sky["id"], "image": sky["file"], "name": sky["name"], "yaw": yaw,
                               "swap_lr": bool(swap_lr), "flip": bool(flip), "targets": picked,
                               "targets_v": CUSTOMIZE_SCAN_V}
        mutate_state(change, f"Sky: {sky['name']}", _sub_progress(progress, 0.4, 1.0))
        return f"“{sky['name']}” applied to {len(picked)} sky textures."
    return start_job("sky", job)


def reset_sky():
    def job(progress):
        def change(state):
            state["skybox"] = None
        mutate_state(change, "Reset sky", progress)
        return "Original skies restored."
    return start_job("sky", job)


def rescan_customize():
    def job(progress):
        cat = scan_customize(progress)
        return (f"Found {sum(len(v) for v in cat['gui'].values())} GUI textures and "
                f"{len(cat['sky'])} sky textures.")
    return start_job("scan", job)


# ------------------------------------------------- PG3D updates + mod re-apply
GAME_UPDATING_FLAGS = 256 | 1024 | 131072 | 262144 | 524288 | 1048576 | 2097152 | 4194304


def steam_app_manifest():
    app = find_app()
    if not app:
        return None
    for parent in app.parents:
        if parent.name == "steamapps":
            p = parent / f"appmanifest_{STEAM_APP_ID}.acf"
            return p if p.exists() else None
    return None


def game_update_info():
    """What Steam knows about a pending PG3D update (read from its app manifest)."""
    def read():
        p = steam_app_manifest()
        if not p:
            return {"known": False, "available": False, "updating": False}
        st = ci_get(load_vdf(p, {}), "AppState", {}) or {}
        def num(k):
            v = str(ci_get(st, k, "0") or "0").strip()
            return int(v) if v.isdigit() else 0
        flags = num("StateFlags")
        build, target = str(ci_get(st, "buildid", "") or ""), str(ci_get(st, "TargetBuildID", "") or "")
        to_dl, done = num("BytesToDownload"), num("BytesDownloaded")
        updating = bool(flags & GAME_UPDATING_FLAGS)
        available = bool(flags & 2) or (target not in ("", "0") and target != build)
        return {"known": True, "available": available or updating, "updating": updating, "build": build, "target": target,
                "progress": round(done / to_dl * 100) if to_dl else None, "size": to_dl or None}
    try:
        return cached("game_update", 4, read)
    except Exception as e:
        log(f"Reading Steam's app manifest failed: {e}")
        return {"known": False, "available": False, "updating": False}


def install_game_update():
    if game_running():
        raise RuntimeError("Close Pixel Gun 3D first.")
    if launch_busy():
        raise RuntimeError("PG3D is starting.")
    # Steam's file check downloads the pending update and verifies every file; the client
    # re-applies its mods by itself once Steam is done.
    subprocess.Popen(["/usr/bin/open", f"steam://validate/{STEAM_APP_ID}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    set_status(game_update_requested=time.time())
    invalidate("game_update")
    log("Asked Steam to install the PG3D update")
    return {"ok": True}


def _match_item(old, items):
    """Find the same sprite/texture again in a fresh scan after a game update."""
    def key(x, rel=True):
        return ((x.get("relative_file") if rel else None), x.get("name"), int(x.get("width", 0)), int(x.get("height", 0)))
    for rel in (True, False):
        hits = [x for x in items if key(x, rel) == key(old, rel)]
        if len(hits) == 1 or (hits and rel):
            return hits[0]
    return None


def _remap_state(state, progress):
    """Point every saved change at the matching asset in the updated game files."""
    new = copy.deepcopy(state)
    lost = []
    if new.get("cursor"):
        progress(.05, "Finding the cursor in the updated game…")
        new["cursor"]["target"] = scan_cursor_target(force=True)
    if new.get("gui") or new.get("hud_set"):
        progress(.1, "Finding your HUD changes in the updated game…")
        items = scan_gui_assets(deep=False)["items"]
        for field in ("gui", "hud_set"):
            remapped = {}
            for op in (new.get(field) or {}).values():
                hit = _match_item(op, items)
                if not hit:
                    lost.append(op.get("name") or "sprite")
                    continue
                op.update(source_file=hit["source_file"], relative_file=hit["relative_file"], path_id=int(hit["path_id"]),
                          key=hit["key"], asset_id=int(hit["id"]), pivot_x=float(hit.get("pivot_x", .5)),
                          pivot_y=float(hit.get("pivot_y", .5)))
                remapped[hit["key"]] = op
            new[field] = remapped
    if new.get("theme") or new.get("skybox") or new.get("loading") or new.get("font"):
        cat = scan_customize(_sub_progress(progress, .35, .7))
        if new.get("theme"):
            new["theme"]["targets"] = theme_targets(cat)
        if new.get("skybox"):
            old = new["skybox"].get("targets", [])
            picked = [t for t in cat.get("sky", []) if any(_match_item(o, [t]) for o in old)] or cat.get("sky", [])
            new["skybox"]["targets"] = [{k: t[k] for k in ("source_file", "relative_file", "path_id", "name", "type", "face", "width", "height")} for t in picked]
            if not picked:
                new["skybox"] = None
                lost.append("sky")
        for key, fields in (("loading", ("source_file", "relative_file", "path_id", "name", "width", "height")),
                            ("font", ("source_file", "relative_file", "path_id", "name"))):
            if new.get(key):
                found = cat.get("loading" if key == "loading" else "fonts", [])
                new[key]["targets"] = [{k: t[k] for k in fields} for t in found]
                if not found:
                    new[key] = None
                    lost.append("loading screen" if key == "loading" else "font")
    return new, lost


def _reapply_work(progress, reason):
    before = load_mods()
    state, lost = (_remap_state(before, progress) if reason == "update" else (before, []))
    rebuild_sources(sources_for_state(state) | {s for s in sources_for_state(before) if Path(s).exists()}, state,
                    _sub_progress(progress, .7, 1.0))
    save_mods(state)
    invalidate("game_update")
    msg = "Your mods were re-applied after the PG3D update." if reason == "update" else "Your mods were re-applied."
    if lost:
        msg += f" Not found in the new version: {', '.join(sorted(set(lost))[:4])}."
    set_status(mods_reapplied={"at": int(time.time()), "reason": reason, "message": msg})
    log(f"Mods re-applied ({reason}); lost: {lost}")
    return msg


@_exclusive
def _reapply_locked(progress, reason):
    return _reapply_work(progress, reason)


def start_reapply(reason):
    return start_job("reapply", lambda progress: _reapply_locked(progress, reason))


def reapply_enabled():
    return prefs_payload().get("reapply_mods", True) is not False


def set_reapply_pref(on):
    prefs = prefs_payload()
    prefs["reapply_mods"] = bool(on)
    write_json(PREFS_JSON, prefs)
    return {"ok": True, "reapply_mods": bool(on)}


def _has_retired_mods(state):
    return bool(state.get("theme") or state.get("hud_set") or state.get("hud_disabled")
                or any(not v.get("role") for v in (state.get("gui") or {}).values()))


def retire_removed_mods():
    """1.47 removed in-game themes and HUD resizing: take any that are still
    applied out of the game files once, as soon as PG3D is closed."""
    while True:
        try:
            if not _has_retired_mods(load_mods()):
                return
            if not _game_running_uncached() and not jobs_pending():
                def change(state):
                    state["theme"] = None
                    state["hud_set"] = {}
                    state["hud_disabled"] = False
                    state["gui"] = {k: v for k, v in (state.get("gui") or {}).items() if v.get("role")}
                mutate_state(change, "Remove in-game theme and HUD changes")
                log("Removed the in-game theme / HUD changes (features retired in 1.47).")
                return
        except Exception as e:
            log(f"Retiring theme/HUD changes failed, retrying: {e}")
        time.sleep(30)


def mods_active(state=None):
    st = state or load_mods()
    return any(st.get(k) for k in ("cursor", "gui", "theme", "skybox", "loading", "font", "hud_set"))


def restore_game_files():
    """Put every game file the client changed back to its original copy. Your
    settings stay in the undo history; apply things again one by one later."""
    def job(progress):
        def change(state):
            state.clear()
            state.update(default_mods())
        mutate_state(change, "Restore original game files", progress)
        set_status(quick_exit=None)
        return "Original game files restored. Start PG3D to check it opens."
    return start_job("restore", job)


def _needs_target_repair(state):
    return any((state.get(k) or {}).get("targets") and (state.get(k) or {}).get("targets_v", 1) < CUSTOMIZE_SCAN_V
               for k in ("skybox", "loading"))


def repair_texture_targets():
    """Sky and loading-screen picks saved before scan v3 may include weapon
    animation textures. Rescan once, keep only real targets and rebuild, which
    restores everything else from the original files."""
    while True:
        try:
            if not _needs_target_repair(load_mods()):
                return
            if not _game_running_uncached() and not launch_busy() and not jobs_pending():
                def job(progress):
                    cat = ensure_catalog(_sub_progress(progress, 0, 0.6))
                    keep_sky = {(t["source_file"], int(t["path_id"])) for t in cat.get("sky", [])}
                    loading = [{k: t[k] for k in ("source_file", "relative_file", "path_id", "name", "width", "height")}
                               for t in _loading_default(cat.get("loading", [])) if Path(t["source_file"]).exists()]
                    report = {}

                    def change(state):
                        sky = state.get("skybox")
                        if sky and sky.get("targets"):
                            before = len(sky["targets"])
                            sky["targets"] = [t for t in sky["targets"] if (t["source_file"], int(t["path_id"])) in keep_sky]
                            if not sky["targets"]:
                                sky["targets"] = [{k: t[k] for k in ("source_file", "relative_file", "path_id", "name",
                                                                     "type", "face", "width", "height")}
                                                  for t in cat.get("sky", []) if Path(t["source_file"]).exists()]
                            sky["targets_v"] = CUSTOMIZE_SCAN_V
                            report["sky"] = (before, len(sky["targets"]))
                        ld = state.get("loading")
                        if ld and ld.get("targets"):
                            report["loading"] = (len(ld["targets"]), len(loading))
                            if loading:
                                ld["targets"], ld["targets_v"] = loading, CUSTOMIZE_SCAN_V
                            else:
                                state["loading"] = None
                    mutate_state(change, "Repair sky / loading screen targets", _sub_progress(progress, 0.6, 1.0))
                    log(f"Repaired texture targets (before -> after): {report}")
                    return "Fixed textures that were changed by mistake (weapon animations)."
                start_job("repair", job)
                return
        except Exception as e:
            log(f"Texture target repair failed, retrying: {e}")
        time.sleep(30)


def mods_reapply_watch():
    """Put the client's mods back by themselves after Steam updates or repairs PG3D."""
    while True:
        time.sleep(20)
        try:
            if not reapply_enabled() or game_running() or launch_busy() or jobs_pending():
                continue
            if _MUTATE_LOCK.locked() or game_update_info().get("updating"):
                continue
            reason = mods_need_reapply()
            if reason:
                log(f"Mods missing from game files ({reason}); re-applying")
                start_reapply(reason)
        except Exception as e:
            log(f"Mod re-apply check failed: {e}")


# ----------------------------------------------------------------------- rank
def default_win_config():
    return {"patterns": list(ranks.DEFAULT_WIN_PATTERNS), "loss_guard": ranks.DEFAULT_LOSS_GUARD,
            "cooldown": 90, "auto": True, "steam_playtime": True}


def wins_data():
    data = read_json(WINS_JSON, {})
    if not isinstance(data.get("accounts"), dict):
        data["accounts"] = {}
    cfg = default_win_config()
    cfg.update(data.get("config") or {})
    data["config"] = cfg
    return data


def win_config():
    cfg = wins_data()["config"]
    return cfg if cfg.get("auto", True) else {**cfg, "patterns": []}


def record_win(account, source, line=None, delta=1):
    with _LOCK:
        data = wins_data()
        acc = data["accounts"].setdefault(str(account), {"auto": 0, "manual": 0, "log": []})
        key = "auto" if source == "auto" else "manual"
        acc[key] = max(0, int(acc.get(key, 0)) + int(delta))
        acc["log"] = (acc.get("log", []) + [{"at": int(time.time()), "source": source, "delta": int(delta),
                                             "line": (line or "")[:300]}])[-300:]
        write_json(WINS_JSON, data)
    log(f"Win recorded ({source}, {delta:+d}) for {account}")
    write_overlay_rank(account)
    return {"ok": True}


def _on_auto_win(line):
    record_win(_playtime_account(), "auto", line)


def steam_playtime_minutes(steamid64):
    def read():
        aid = steam_account_id(steamid64)
        if aid is None:
            return None
        path = steam_root() / "userdata" / str(aid) / "config/localconfig.vdf"
        if not path.exists():
            return None
        try:
            node = load_vdf(path, {})
            for key in ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps", STEAM_APP_ID):
                node = ci_get(node, key, {}) or {}
            value = ci_get(node, "Playtime")
            return int(value) if value is not None else None
        except Exception as e:
            log(f"Steam playtime read failed: {e}")
            return None
    return cached(("steam_playtime", str(steamid64)), 60, read)


EMBER_LOOKUP_URL = "https://emberpg3d.com/api/utility/pub/{}"


def _ember_fetch(player_id, full=False):
    """Public, read-only Ember player lookup (no login). Returns a summary,
    plus the untouched response under "full" when asked."""
    req = Request(EMBER_LOOKUP_URL.format(player_id),
                  headers={"User-Agent": f"PG3D-Mac-Client/{APP_VERSION}", "Accept": "application/json"})
    with urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    profile = data.get("profile") or {}
    v3 = data.get("v3") or {}
    gw = profile.get("global_wins") or {}
    total = sum(int(v) for k, v in gw.items() if k not in ("t", "weekly") and str(v).lstrip("-").isdigit())
    weekly = profile.get("weekly_stats") or {}
    if not profile and not gw:
        raise RuntimeError("Ember has no stats for that player ID. Check the ID in your PG3D profile.")
    return {
        "player_id": str(player_id),
        "name": profile.get("name") or v3.get("name") or data.get("name") or "",
        "level": profile.get("level") or v3.get("level") or data.get("level"),
        "wins_total": total,
        "wins_by_mode": {k: v for k, v in gw.items() if k not in ("t", "weekly")},
        "weekly": {k: weekly.get(k) for k in ("kills", "deaths", "matches", "wins", "headshots",
                                               "killstreak", "winstreak", "score")},
        "banned": data.get("banned"),
        "clan": profile.get("clan_name") or None,
        # Ember's "clan_tag" is the clan emblem as a base64 PNG, not text.
        "clan_logo": _png_b64(profile.get("clan_tag")),
        "clan_tag": None if _png_b64(profile.get("clan_tag")) else (str(profile.get("clan_tag") or "")[:12] or None),
        "registered": profile.get("registered"),
        "last_seen": profile.get("last_seen"),
        "items": {k: profile.get(f"{k}_count") for k in ("weapon", "gadget", "pet", "avatar", "sets")},
        "skin": profile.get("skin") or v3.get("equipped_skin"),
        "fetched": int(time.time()),
        **({"full": data} if full else {}),
    }


EMBER_REFRESH_PLAYING = 60     # seconds between refreshes while PG3D runs
EMBER_REFRESH_IDLE = 600


def ember_stats(account, force=False, fetch=True):
    """Linked Ember stats for a Steam account.

    With fetch=False this only returns what is cached; the sync thread does
    the network refreshes so the status poll never waits on Ember.
    """
    data = wins_data()
    acc = data["accounts"].get(str(account), {})
    link = acc.get("ember")
    if not link or not link.get("player_id"):
        return None
    age = time.time() - int(link.get("fetched", 0) or 0)
    limit = EMBER_REFRESH_PLAYING if game_running() else EMBER_REFRESH_IDLE
    if not fetch or (not force and age < limit):
        return link
    try:
        fresh = _ember_fetch(link["player_id"])
        fresh.pop("skin", None)
        fresh["error"] = None
    except Exception as e:
        log(f"Ember lookup failed: {e}")
        fresh = {**link, "error": str(e), "fetched": int(time.time())}
    fresh["source"] = link.get("source", "manual")
    with _LOCK:
        data = wins_data()
        data["accounts"].setdefault(str(account), {"auto": 0, "manual": 0, "log": []})["ember"] = fresh
        write_json(WINS_JSON, data)
    return fresh


def link_ember(account, player_id, source="manual"):
    pid = re.sub(r"\s+", "", str(player_id or ""))
    data = wins_data()
    acc = data["accounts"].setdefault(str(account), {"auto": 0, "manual": 0, "log": []})
    if not pid:
        acc.pop("ember", None)
        acc["ember_auto_off"] = True  # the user unlinked: don't re-link behind their back
        write_json(WINS_JSON, data)
        return {"ok": True, "linked": False}
    if not re.fullmatch(r"\d{1,12}", pid):
        raise RuntimeError("A PG3D player ID is numbers only. Find it in your in-game profile.")
    stats = _ember_fetch(pid)  # raises with a readable message if the ID has no stats
    stats.pop("skin", None)
    stats["error"] = None
    stats["source"] = source
    remember_pg3d_id(account, pid, stats.get("name"))
    with _LOCK:
        data = wins_data()
        acc = data["accounts"].setdefault(str(account), {"auto": 0, "manual": 0, "log": []})
        acc["ember"] = stats
        acc.pop("ember_auto_off", None)
        write_json(WINS_JSON, data)
    log(f"Ember linked for {account}: {pid} ({source})")
    return {"ok": True, "linked": True, "ember": stats}


# ------------------------------------------------- PG3D player ID detection
# Unity keeps PlayerPrefs for a macOS app in ~/Library/Preferences/<domain>.plist.
# PG3D stores the signed-in player ID under "AccountCreated" (the same value
# the Windows build keeps in the registry). Only that key is ever read here.
PG3D_ID_KEY = "AccountCreated"


def _pg3d_pref_domains():
    prefs = Path.home() / "Library/Preferences"
    found = []
    try:
        for p in prefs.glob("*.plist"):
            low = p.name.lower()
            if "pixel gun" in low or "pixelgun" in low or "pixel.gun" in low:
                found.append(p)
    except OSError:
        pass
    return sorted(found, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def _read_prefs(path: Path):
    # `defaults export` goes through cfprefsd, so it sees values the game has
    # written but macOS hasn't flushed to disk yet. Fall back to the file.
    try:
        r = subprocess.run(["/usr/bin/defaults", "export", path.stem, "-"], capture_output=True, timeout=5)
        if r.returncode == 0 and r.stdout:
            return plistlib.loads(r.stdout)
    except Exception:
        pass
    try:
        with path.open("rb") as f:
            return plistlib.load(f)
    except Exception:
        return {}


def _id_from_value(value):
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", "ignore").rstrip("\x00")
    m = re.search(r"\d{4,12}", str(value))
    return m.group(0) if m else None


def detect_pg3d_player_id():
    """The PG3D player ID currently signed in on this Mac, or None."""
    for path in _pg3d_pref_domains():
        prefs = _read_prefs(path)
        for key, value in prefs.items():
            # Unity on Windows appends "_h<hash>" to key names; macOS may not.
            if key == PG3D_ID_KEY or key.startswith(PG3D_ID_KEY + "_h"):
                pid = _id_from_value(value)
                if pid:
                    return pid
    return None


def remember_pg3d_id(account, pid, name=None):
    """Remember which PG3D player ID was seen on which Steam account."""
    if not account or account == "unknown" or not pid:
        return
    with _LOCK:
        data = wins_data()
        ids = data.setdefault("pg3d_ids", {})
        prev = ids.get(str(account)) or {}
        if prev.get("player_id") == str(pid) and (not name or prev.get("name") == name):
            return
        ids[str(account)] = {"player_id": str(pid), "name": name or prev.get("name"), "seen": int(time.time())}
        write_json(WINS_JSON, data)


_LOOKUP_CACHE = {}
EMBER_ICON_DIR = SUPPORT / "ember_icons"
EMBER_ICON_FOLDERS = {"Weapons", "Armor", "WeaponSkin", "Pet", "Lookup", "Currency"}


def ember_icon_url(folder, name):
    if folder not in EMBER_ICON_FOLDERS or not re.fullmatch(r"[A-Za-z0-9 _-]{1,40}", name):
        return None
    return "https://emberpg3d.com/icons/{}/{}.png".format(folder, name.replace(" ", "%20"))


def ember_icon_cached(folder, name):
    """Path if the icon is cached, "missing" if Ember has none, else None."""
    if not ember_icon_url(folder, name):
        return "missing"
    path = EMBER_ICON_DIR / folder / f"{name}.png"
    if path.is_file():
        return path
    miss = path.with_suffix(".missing")
    if miss.exists() and time.time() - miss.stat().st_mtime < 7 * 86400:
        return "missing"
    return None


def ember_icon(folder, name):
    """Item/stat icons from Ember's site, cached on disk. None if missing."""
    if folder not in EMBER_ICON_FOLDERS or not re.fullmatch(r"[A-Za-z0-9 _-]{1,40}", name):
        return None
    path = EMBER_ICON_DIR / folder / f"{name}.png"
    miss = path.with_suffix(".missing")
    if path.is_file():
        return path
    if miss.exists() and time.time() - miss.stat().st_mtime < 7 * 86400:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        url = "https://emberpg3d.com/icons/{}/{}.png".format(folder, name.replace(" ", "%20"))
        req = Request(url, headers={"User-Agent": f"PG3D-Mac-Client/{APP_VERSION}"})
        with urlopen(req, timeout=8) as r:
            data = r.read(2 * 1024 * 1024)
            ctype = r.headers.get("Content-Type", "")
        if not ctype.startswith("image/png") or not data.startswith(b"\x89PNG"):
            miss.touch()
            return None
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path
    except Exception:
        return None


def _png_b64(value):
    """value if it is a base64-encoded PNG (small), else None."""
    v = str(value or "")
    return v if v.startswith("iVBORw0KGgo") and len(v) < 200_000 else None


def lookup_player(pid):
    pid = re.sub(r"\s+", "", str(pid or ""))
    if not re.fullmatch(r"\d{1,12}", pid):
        raise RuntimeError("A PG3D player ID is numbers only.")
    hit = _LOOKUP_CACHE.get(pid)
    if hit and time.time() - hit["fetched"] < 120:
        return hit
    data = _ember_fetch(pid, full=True)
    _LOOKUP_CACHE[pid] = data
    return data


def lookup_suggestions():
    """Every Steam account on this Mac with the PG3D ID last seen on it."""
    try:
        info = steam_accounts()
    except Exception:
        info = {"accounts": []}
    ids = wins_data().get("pg3d_ids", {})
    out = []
    for a in info.get("accounts", []):
        known = ids.get(a["steamid64"]) or {}
        out.append({
            "steamid64": a["steamid64"], "steam_name": a.get("display_name") or a.get("account_name"),
            "active": bool(a.get("active")), "player_id": known.get("player_id"), "name": known.get("name"),
        })
    listed = {x["steamid64"] for x in out}
    for sid, known in ids.items():  # accounts no longer in Steam's list
        if sid not in listed:
            out.append({"steamid64": sid, "steam_name": None, "active": False,
                        "player_id": known.get("player_id"), "name": known.get("name")})
    out.sort(key=lambda x: (not x["active"], x["player_id"] is None, (x["steam_name"] or "").lower()))
    return {"ok": True, "accounts": out}


def _trusted_player_id(account, pid, running, running_for):
    """The detected PG3D ID, or None while it can't be trusted for this Steam account."""
    if not pid:
        return None
    if running and running_for < 45:
        return None  # the game hasn't signed in yet; the saved ID may be stale
    owner = next((sid for sid, v in (wins_data().get("pg3d_ids") or {}).items()
                  if sid != str(account) and (v or {}).get("player_id") == str(pid)), None)
    if owner:
        return None  # that ID belongs to another Steam account on this Mac
    return pid


def ember_sync_watch():
    """Keep the active Steam account linked to the PG3D ID that is signed in,
    and refresh its Ember wins every minute while the game runs."""
    was_running = False
    last_detect = 0.0
    run_start = 0.0
    seen = (None, None, 0)  # (account, pid, consecutive sightings)
    while True:
        try:
            running = _game_running_uncached()
            account = _playtime_account()
            now = time.time()
            if running and not was_running:
                run_start, seen = now, (None, None, 0)
            if account != "unknown" and (running or was_running) and now - last_detect > 30:
                last_detect = now
                pid = _trusted_player_id(account, detect_pg3d_player_id(), running, now - run_start)
                seen = (account, pid, seen[2] + 1) if pid and seen[:2] == (account, pid) else (account, pid, 1)
                # Right after a switch PG3D still has the previous account's ID saved
                # until it signs in, so only trust an ID that stays put.
                if seen[2] < 2:
                    pid = None
                if pid:
                    remember_pg3d_id(account, pid)
                acc = wins_data()["accounts"].get(str(account), {})
                linked = (acc.get("ember") or {}).get("player_id")
                if pid and pid != linked and not (acc.get("ember_auto_off") and not linked):
                    try:
                        link_ember(account, pid, source="detected")
                    except Exception as e:
                        log(f"Auto-link of PG3D ID {pid} failed: {e}")
            if account != "unknown":
                # force one refresh right after a session ends so the last
                # match's wins land without waiting for the idle interval
                ember_stats(account, force=(was_running and not running))
            was_running = running
        except Exception as e:
            log(f"Ember sync error: {e}")
        time.sleep(10)


def tracked_minutes(account):
    data = read_json(PLAYTIME_JSON, {"sessions": []})
    total = sum(x.get("seconds", 0) for x in data.get("sessions", [])
                if isinstance(x, dict) and x.get("account") == account)
    with _LOCK:
        live_start = STATUS.get("session_start")
    if live_start:
        total += time.time() - live_start
    return total / 60.0


def rank_payload(account, full=False):
    data = wins_data()
    cfg = data["config"]
    acc = data["accounts"].get(str(account), {})
    auto_w, manual_w = int(acc.get("auto", 0)), int(acc.get("manual", 0))
    ember = None
    try:
        ember = ember_stats(account, fetch=False)
    except Exception as e:
        log(f"Ember stats failed: {e}")
    if ember and ember.get("wins_total") is not None and ember.get("player_id"):
        # Server-side all-time wins replace log detection (no double counting);
        # manual +/- still apply as corrections.
        wins_source = "ember"
        wins = max(0, int(ember["wins_total"]) + manual_w)
    else:
        wins_source = "local"
        wins = auto_w + manual_w
    tracked = tracked_minutes(account)
    steam_min = steam_playtime_minutes(account) if cfg.get("steam_playtime", True) else None
    minutes = max(tracked, float(steam_min or 0))
    xp = ranks.xp_for(minutes, wins)
    info = ranks.rank_info(xp, wins)

    with _LOCK:
        rs = read_json(RANK_STATE_JSON, {})
        st = rs.get(str(account))
        changed = False
        now = int(time.time())
        # v2 = tier-only ranks (steps 0..8). Older state used divisions, so start fresh
        # without counting the conversion itself as a rank-up.
        if st is None or st.get("v") != 2:
            st = {"v": 2, "best_step": info["step"], "seen_at": now,
                  "history": [{"step": info["step"], "title": info["title"], "at": now, "kind": "placed"}]}
            changed = True
        elif info["step"] > int(st.get("best_step", 0)):
            st["history"] = (st.get("history", []) + [{"step": info["step"], "from_step": int(st.get("best_step", 0)),
                                                       "title": info["title"], "at": now, "kind": "promoted"}])[-100:]
            st["best_step"] = info["step"]
            changed = True
        if changed:
            rs[str(account)] = st
            write_json(RANK_STATE_JSON, rs)
    last = (st.get("history") or [None])[-1]
    # Rank-ups the player hasn't watched yet (the animation plays once they look at the client).
    unseen = [h for h in st.get("history", []) if h.get("kind") == "promoted" and h.get("at", 0) > st.get("seen_at", 0)]
    pending = None
    if unseen:
        lo = min(int(h.get("from_step", h["step"] - 1)) for h in unseen)
        hi = max(int(h["step"]) for h in unseen)
        path = [ranks.TIERS[ranks.STEPS[i][0]] for i in range(max(0, lo), hi + 1)]
        pending = {"at": max(h["at"] for h in unseen),
                   "tiers": [{"tier": t[0], "name": t[1], "color": ranks.TIER_COLORS[t[0]],
                              "emblem": f"/ranks/{t[0]}.png"} for t in path]}

    out = {
        **info,
        "account": account,
        "wins": wins,
        "wins_auto": auto_w,
        "wins_manual": manual_w,
        "wins_source": wins_source,
        "ember": ember,
        "minutes": int(minutes),
        "hours": round(minutes / 60.0, 1),
        "tracked_minutes": int(tracked),
        "steam_minutes": steam_min,
        "playtime_source": "steam" if steam_min and steam_min >= tracked else "client",
        "best_step": st.get("best_step", info["step"]),
        "last_event": last,
        "pending": pending,
        "emblem": f"/ranks/{info['tier']}.png",
    }
    if full:
        out.update({
            "ladder": ranks.ladder(),
            "xp_per_minute": ranks.XP_PER_MINUTE,
            "xp_per_win": ranks.XP_PER_WIN,
            "typical_xp_per_hour": ranks.TYPICAL_XP_PER_HOUR,
            "history": list(reversed(st.get("history", [])))[:30],
            "win_log": list(reversed(acc.get("log", [])))[:30],
            "config": cfg,
            "player_log": str(ranks.find_player_log() or ""),
            "player_log_candidates": [str(p) for p in ranks.player_log_candidates()[:3]],
            "recent_lines": list(reversed(WIN_WATCHER.recent_lines())) if WIN_WATCHER else [],
        })
    return out


def mark_rank_seen(account, at):
    with _LOCK:
        rs = read_json(RANK_STATE_JSON, {})
        st = rs.get(str(account))
        if st:
            st["seen_at"] = max(int(st.get("seen_at", 0)), int(at or time.time()))
            write_json(RANK_STATE_JSON, rs)
    return {"ok": True}


def set_win_config(raw):
    data = wins_data()
    cfg = data["config"]
    if "patterns" in raw:
        pats = [str(p).strip() for p in (raw.get("patterns") or []) if str(p).strip()][:12]
        for pat in pats:
            try:
                re.compile(pat)
            except re.error as e:
                raise RuntimeError(f"Pattern “{pat[:40]}” is not a valid regular expression: {e}")
        cfg["patterns"] = pats
    if "loss_guard" in raw:
        guard = str(raw.get("loss_guard") or "").strip()
        if guard:
            try:
                re.compile(guard)
            except re.error as e:
                raise RuntimeError(f"Loss filter is not a valid regular expression: {e}")
        cfg["loss_guard"] = guard
    if "cooldown" in raw:
        cfg["cooldown"] = max(20, min(900, int(raw.get("cooldown") or 90)))
    for key in ("auto", "steam_playtime"):
        if key in raw:
            cfg[key] = bool(raw[key])
    if raw.get("reset_defaults"):
        cfg = default_win_config()
    data["config"] = cfg
    write_json(WINS_JSON, data)
    invalidate()
    return {"ok": True, "config": cfg}


def test_win_patterns(patterns, guard):
    lines = [x["line"] for x in (WIN_WATCHER.recent_lines() if WIN_WATCHER else [])]
    compiled = []
    for pat in patterns or []:
        try:
            compiled.append(re.compile(str(pat), re.I))
        except re.error as e:
            raise RuntimeError(f"Invalid pattern: {e}")
    g = re.compile(guard, re.I) if guard else None
    hits = [ln for ln in lines if any(c.search(ln) for c in compiled) and not (g and g.search(ln))]
    return {"ok": True, "checked": len(lines), "hits": hits[-20:]}


WIN_WATCHER = None


def initialize_cursor(force=False):
    try:
        scan_cursor_target(force=force)
    except Exception as e:
        log(traceback.format_exc())
        set_status(cursor_phase="error", cursor_progress=0, cursor_message="Cursor detection failed.",
                   cursor_error=str(e), cursor_target=None)


def start_cursor_init(force=False):
    with _LOCK:
        if STATUS.get("cursor_phase") == "scanning":
            return
        set_status(cursor_phase="scanning", cursor_progress=0,
                   cursor_message="Locating cursor asset #3…", cursor_error=None)
    threading.Thread(target=initialize_cursor, args=(force,), daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    server_version = f"PG3DMacClient/{APP_VERSION}"

    def log_message(self, fmt, *args):
        return

    def send_json(self, obj, status=200):
        if isinstance(obj, dict) and getattr(_REQ, "queued", False) and status == 200:
            obj = {**obj, "queued": True}  # the change went onto the job queue
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def send_bytes(self, data: bytes, content_type="application/octet-stream", cache=False):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path: Path, cache=False):
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_file_range(self, path: Path, ctype: str):
        """Serve a file with HTTP byte ranges (WebKit needs this to play MP4)."""
        if not path.is_file():
            return self.send_error(404)
        size = path.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range", "")
        m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
        partial = bool(m) and (m.group(1) or m.group(2))
        if partial:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        try:
            with path.open("rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(1 << 20, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def read_body(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(n) if n else b"{}"
        return json.loads(body.decode("utf-8")) if body else {}

    def do_GET(self):
        global LAST_REQUEST
        LAST_REQUEST = time.time()
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/health":
            return self.send_json({"ok": True, "app": APP_ID, "version": APP_VERSION, "base": str(BASE), "pid": os.getpid(), "fps_bundle_present": fps_bundle_present()})
        if path == "/api/status":
            return self.send_json(status_payload())
        if path == "/api/overlay/config":
            return self.send_json({"ok": True, "config": overlay_config(), "runtime": overlay_runtime()})
        if path == "/api/clips":
            return self.send_json(clips.payload())
        if path == "/api/settings":
            return self.send_json(settings_payload())
        if path.startswith("/clips/strip/"):
            name = unquote(path[len("/clips/strip/"):])
            if "/" in name or "\\" in name or not name.endswith(".strip.jpg") or name.startswith("."):
                return self.send_error(403)
            return self.send_file(clips.THUMBS_DIR / name)
        if path.startswith("/clips/video/"):
            try:
                clip = clips.safe_clip_path(unquote(path[len("/clips/video/"):]))
            except RuntimeError:
                return self.send_error(403)
            return self.send_file_range(clip, "video/mp4")
        if path.startswith("/clips/thumb/"):
            name = unquote(path[len("/clips/thumb/"):])
            if "/" in name or "\\" in name or not name.endswith(".jpg") or name.startswith("."):
                return self.send_error(403)
            return self.send_file(clips.THUMBS_DIR / name)
        if path == "/api/steam-player-count":
            return self.send_json(steam_player_count())
        if path == "/api/gui-assets":
            return self.send_json(gui_catalog())
        if path == "/api/sound/custom":
            return self.send_json({"sounds": custom_sounds()})
        if path.startswith("/api/sound/custom/"):
            sid = Path(path).name
            hit = next((x for x in custom_sounds() if x["id"] == sid), None)
            return self.send_file(CUSTOM_SOUNDS_DIR / hit["file"], cache=True) if hit else self.send_error(404)
        if path == "/api/sound/rankup":
            f = _rankup_file()
            return self.send_file(f, cache=True) if f else self.send_error(404)
        if path == "/api/sprites":
            return self.send_json(sprite_roles_payload())
        if path.startswith("/api/sprites/file/"):
            parts = path.split("/")
            if len(parts) != 6 or parts[4] not in SPRITE_ROLES or not re.fullmatch(r"[0-9a-f]{12}\.png", parts[5]):
                return self.send_error(403)
            return self.send_file(ROLE_DIR / parts[4] / parts[5])
        if path.startswith("/api/ember-icon/"):
            parts = unquote(path[len("/api/ember-icon/"):]).split("/")
            if len(parts) != 2 or not parts[1].endswith(".png"):
                return self.send_error(404)
            folder, name = parts[0], parts[1][:-4]
            cached_icon = ember_icon_cached(folder, name)
            if cached_icon == "missing":
                return self.send_error(404)
            if cached_icon:
                return self.send_file(cached_icon, cache=True)
            # Not cached yet: let the browser load it from Ember directly (so slow
            # first downloads don't tie up connections to this server) and save
            # a copy in the background for next time.
            url = ember_icon_url(folder, name)
            if not url:
                return self.send_error(404)
            threading.Thread(target=ember_icon, args=(folder, name), daemon=True).start()
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/lookup/suggest":
            return self.send_json(lookup_suggestions())
        if path.startswith("/api/lookup/"):
            try:
                return self.send_json({"ok": True, "player": lookup_player(unquote(path.rsplit("/", 1)[1]))})
            except Exception as e:
                return self.send_json({"error": str(e)}, 400)
        if path == "/api/rank":
            return self.send_json(rank_payload(_playtime_account(), full=True))
        if path == "/api/theme":
            return self.send_json({**theme_payload(), "font": font_payload()})
        if path.startswith("/api/font-file/"):
            fid = Path(path).name
            try:
                return self.send_file(font_path(_font_by_id(fid)), cache=True)
            except Exception:
                return self.send_error(404)
        if path.startswith("/api/theme/sample/"):
            name = Path(path).name
            if not re.fullmatch(r"\d{2}\.png", name):
                return self.send_error(403)
            return self.send_file(THEME_SAMPLES / name)
        if path == "/api/theme/shot.jpg":
            try:
                return self.send_file(render_theme_shot((query.get("preset") or [""])[0]), cache=True)
            except Exception as e:
                log(f"Theme screenshot preview failed: {e}")
                return self.send_error(404)
        if path == "/api/theme/preview.png":
            try:
                raw = render_theme_sample((query.get("i") or ["0"])[0], (query.get("preset") or [""])[0],
                                          (query.get("strength") or ["0.85"])[0])
                return self.send_bytes(raw, "image/png")
            except Exception as e:
                log(f"Theme preview failed: {e}")
                return self.send_error(404)
        if path == "/api/sky":
            return self.send_json(sky_payload())
        if path.startswith("/api/sky/thumb/"):
            name = Path(path).name
            if not re.fullmatch(r"[0-9a-f]{16}\.jpg", name):
                return self.send_error(403)
            return self.send_file(SKY_PREVIEWS / name, cache=True)
        if path.startswith("/api/sky/target/"):
            name = Path(path).name
            if not re.fullmatch(r"\d{1,5}\.jpg", name):
                return self.send_error(403)
            return self.send_file(SKY_PREVIEWS / f"target_{name}")
        if path.startswith("/api/sky/loading/"):
            name = Path(path).name
            if not re.fullmatch(r"\d{1,5}\.jpg", name):
                return self.send_error(403)
            return self.send_file(SKY_PREVIEWS / f"loading_{name}")
        if path == "/api/sky/cross.png":
            try:
                raw = sky_cross_preview((query.get("id") or [""])[0], float((query.get("yaw") or ["0"])[0]),
                                        (query.get("swap") or ["0"])[0] == "1")
                return self.send_bytes(raw, "image/png")
            except Exception as e:
                log(f"Sky preview failed: {e}")
                return self.send_error(404)
        if path == "/api/original-cursor.png":
            return self.send_file(RUNTIME / "original_cursor.png")
        if path == "/api/custom-cursor.png":
            return self.send_file(CUSTOM_CURSOR)
        if path == "/api/cursor-preview.png":
            cursor_id = (query.get("cursor") or ["clean_dot"])[0]
            size = (query.get("size") or ["45"])[0]
            color = (query.get("color") or ["#FFFFFF"])[0]
            outline = (query.get("outline") or [""])[0] or None
            ow = (query.get("outline_width") or ["0"])[0]
            try:
                raw, _, _ = render_cursor_preview(cursor_id, size, color, outline, ow)
                return self.send_bytes(raw, "image/png", cache=False)
            except Exception as e:
                log(f"Cursor preview render failed: {e}")
                return self.send_error(500)
        if path.startswith("/api/gui-preview/"):
            name = Path(path).name
            if not re.fullmatch(r"\d{4}\.png", name):
                return self.send_error(403)
            return self.send_file(GUI_PREVIEWS / name, cache=True)
        if path in ("/", "/index.html"):
            return self.send_file(WEB / "index.html")
        rel = path.lstrip("/")
        candidate = (WEB / rel).resolve()
        try:
            candidate.relative_to(WEB.resolve())
        except ValueError:
            return self.send_error(403)
        return self.send_file(candidate, cache=True)

    def do_POST(self):
        global LAST_REQUEST
        LAST_REQUEST = time.time()
        _REQ.queued = False
        path = urlparse(self.path).path
        invalidate()
        try:
            data = self.read_body()
            if path == "/api/cursor/apply":
                return self.send_json(apply_cursor(data.get("cursor", "clean_dot"), data.get("size", 45),
                                                   data.get("color"), data.get("outline"), data.get("outline_width", 0)))
            if path == "/api/cursor/reset":
                return self.send_json(reset_cursor())
            if path == "/api/cursor/custom":
                return self.send_json(import_custom_cursor(data.get("data", "")))
            if path == "/api/cursor/redetect":
                start_cursor_init(force=True)
                return self.send_json({"ok": True})
            if path == "/api/sprites/apply":
                return self.send_json(apply_sprite_role(data.get("role"), str(data.get("id") or ""), data.get("size", 90)))
            if path == "/api/sprites/install":
                return self.send_json(install_role_sprite(data.get("role"), data.get("data"), data.get("name")))
            if path == "/api/sprites/delete":
                return self.send_json(delete_role_sprite(data.get("role"), str(data.get("id") or "")))
            if path == "/api/sprites/reset":
                return self.send_json(reset_sprite_role(data.get("role")))
            if path == "/api/gui/scan":
                start_gui_scan(bool(data.get("deep", False)))
                return self.send_json({"ok": True})
            if path in ("/api/gui/apply", "/api/gui/hud-member", "/api/gui/hud-disabled",
                        "/api/gui/import-hud-selection", "/api/theme/apply"):
                raise RuntimeError("In-game themes and HUD resizing were removed in 1.47.")
            if path == "/api/gui/reset":
                return self.send_json(reset_gui_asset(data.get("asset_id")))
            if path == "/api/gui/reset-all":
                return self.send_json(reset_all_gui())
            if path == "/api/gui/clear-hud-set":
                return self.send_json(clear_hud_set())
            if path == "/api/font/apply":
                return self.send_json(apply_font(str(data.get("id") or "")))
            if path == "/api/font/upload":
                return self.send_json(upload_font(data.get("data"), data.get("name")))
            if path == "/api/theme/reset":
                return self.send_json(reset_theme())
            if path == "/api/customize/scan":
                return self.send_json(rescan_customize())
            if path == "/api/sky/upload":
                return self.send_json(upload_sky(data.get("data", ""), data.get("name", "")))
            if path == "/api/sky/delete":
                return self.send_json(delete_sky(str(data.get("id", ""))))
            if path == "/api/sky/apply":
                ids = data.get("targets")
                ids = [int(x) for x in ids] if isinstance(ids, list) else None
                return self.send_json(apply_sky(str(data.get("id", "")), ids, data.get("yaw", 0),
                                                bool(data.get("swap_lr")), bool(data.get("flip"))))
            if path == "/api/sky/reset":
                return self.send_json(reset_sky())
            if path == "/api/loading/apply":
                return self.send_json(apply_loading(str(data.get("id", "")), data.get("targets") or None))
            if path == "/api/loading/reset":
                return self.send_json(reset_loading())
            if path == "/api/rank/win":
                delta = 1 if int(data.get("delta", 1)) >= 0 else -1
                return self.send_json(record_win(_playtime_account(), "manual", None, delta))
            if path == "/api/rank/ember":
                return self.send_json(link_ember(_playtime_account(), data.get("player_id", "")))
            if path == "/api/rank/ember/detect":
                pid = detect_pg3d_player_id()
                if not pid:
                    raise RuntimeError("No PG3D player ID found yet. Start the game once and sign in, then try again.")
                return self.send_json(link_ember(_playtime_account(), pid, source="detected"))
            if path == "/api/rank/ember/refresh":
                return self.send_json({"ok": True, "ember": ember_stats(_playtime_account(), force=True)})
            if path == "/api/rank/seen":
                return self.send_json(mark_rank_seen(_playtime_account(), data.get("at")))
            if path == "/api/rank/config":
                return self.send_json(set_win_config(data))
            if path == "/api/rank/test":
                return self.send_json(test_win_patterns(data.get("patterns", []), data.get("loss_guard", "")))
            if path == "/api/history/undo":
                return self.send_json(undo_change())
            if path == "/api/history/redo":
                return self.send_json(redo_change())
            if path == "/api/account/select":
                return self.send_json(select_account(str(data.get("steamid64", ""))))
            if path == "/api/account/nickname":
                return self.send_json(set_account_nickname(str(data.get("steamid64", "")), data.get("nickname", "")))
            if path == "/api/account/credentials":
                return self.send_json(set_account_credentials(str(data.get("steamid64", "")), data.get("username", ""), data.get("password", "")))
            if path == "/api/account/credentials/remove":
                return self.send_json(remove_account_credentials(str(data.get("steamid64", ""))))
            if path == "/api/account/guard":
                return self.send_json(submit_steam_guard_code(data.get("code", "")))
            if path == "/api/account/refresh":
                return self.send_json(steam_accounts())
            if path == "/api/overlay/config":
                return self.send_json(set_overlay_config(data))
            if path == "/api/overlay/reset":
                return self.send_json(reset_overlay_config())
            if path == "/api/overlay/edit":
                return self.send_json(begin_overlay_editor(data))
            if path == "/api/prefs/sound":
                return self.send_json(set_sound_prefs(data))
            if path == "/api/prefs/ui-theme":
                return self.send_json(set_ui_theme_pref(str(data.get("theme", ""))))
            if path == "/api/sound/rankup":
                return self.send_json(set_rankup_sound(data.get("data"), data.get("name")))
            if path == "/api/sound/custom":
                return self.send_json(add_custom_sound(data.get("data"), data.get("name")))
            if path == "/api/sound/custom/delete":
                return self.send_json(remove_custom_sound(str(data.get("id", ""))))
            if path == "/api/sound/rankup/reset":
                return self.send_json(reset_rankup_sound())
            if path == "/api/prefs/mouse-fix":
                return self.send_json(set_mouse_fix_pref(data.get("on", True)))
            if path == "/api/game/quit":
                return self.send_json(quit_game())
            if path == "/api/game/restore-files":
                return self.send_json(restore_game_files())
            if path == "/api/game/refocus":
                return self.send_json(refocus_game())
            if path == "/api/game-update/install":
                return self.send_json(install_game_update())
            if path == "/api/prefs/reapply":
                return self.send_json(set_reapply_pref(data.get("on", True)))
            if path == "/api/mods/reapply":
                reason = mods_need_reapply() or "restored"
                return self.send_json(start_reapply(reason))
            if path == "/api/prefs/ui-style":
                return self.send_json(set_ui_style_pref(data))
            if path == "/api/prefs/fps":
                return self.send_json(set_fps_pref(data.get("fps", "uncapped")))
            if path == "/api/clips/config":
                return self.send_json(clips.set_config(data))
            if path == "/api/clips/save":
                return self.send_json(clips.RECORDER.save_clip(data.get("length")))
            if path == "/api/clips/delete":
                return self.send_json(clips.delete_clip(data.get("name")))
            if path == "/api/clips/rename":
                return self.send_json(clips.rename_clip(data.get("name"), data.get("title")))
            if path == "/api/clips/reveal":
                return self.send_json(clips.reveal_clip(data.get("name")))
            if path == "/api/clips/copy":
                return self.send_json(clips.copy_clip(data.get("name")))
            if path == "/api/clips/install-ffmpeg":
                return self.send_json(clips.RECORDER.install_ffmpeg(manual=True))
            if path == "/api/clips/trim":
                return self.send_json(clips.RECORDER.trim(data.get("name"), data.get("start", 0),
                                                          data.get("end", 0), "replace" if data.get("mode") == "replace" else "new"))
            if path == "/api/update/check":
                updater.UPDATER.check_github(force=True)
                updater.UPDATER.scan()
                return self.send_json(settings_payload())
            if path == "/api/update/github":
                updater.set_github_token(data.get("token"))
                if data.get("token"):
                    rel = updater.UPDATER.check_github(force=True)
                    err = updater.UPDATER.github.get("error")
                    if err:
                        updater.set_github_token("")
                        updater.UPDATER.check_github(force=True)
                        raise RuntimeError(err)
                    if rel is None:
                        log("GitHub connected; no release published yet")
                else:
                    updater.UPDATER.check_github(force=True)
                updater.UPDATER.scan()
                return self.send_json(settings_payload())
            if path == "/api/update/install":
                return self.send_json(updater.UPDATER.install())
            if path == "/api/settings/updates":
                updater.save_prefs(data)
                return self.send_json(settings_payload())
            if path == "/api/settings/diagnostics":
                return self.send_json(diagnostics_report())
            if path == "/api/settings/clear-cache":
                return self.send_json(clear_cache())
            if path == "/api/settings/reveal":
                return self.send_json(reveal(data.get("what")))
            if path == "/api/clips/permission":
                return self.send_json(clips.request_screen_permission())
            if path == "/api/clips/screens/refresh":
                clips.RECORDER.screens(refresh=True)
                return self.send_json(clips.payload())
            if path == "/api/launch/normal":
                return self.send_json(start_launch("normal"))
            if path == "/api/launch/fps":
                return self.send_json(start_launch("fps", data.get("fps", prefs_payload().get("fps_target", "uncapped"))))
            return self.send_json({"error": "Unknown endpoint"}, 404)
        except Exception as e:
            log(traceback.format_exc())
            set_status(launch_error=str(e) if path.startswith("/api/launch") else STATUS.get("launch_error"))
            return self.send_json({"error": str(e)}, 500)
        finally:
            invalidate()


def idle_watch(server):
    while True:
        time.sleep(60)
        if time.time() - LAST_REQUEST > 4 * 3600:
            log("Shutting down after four hours of inactivity.")
            server.shutdown()
            return


def cleanup():
    try:
        clips.shutdown()
    except Exception:
        pass
    try:
        if OVERLAY_PID_FILE.exists():
            pid = int(OVERLAY_PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            OVERLAY_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def main():
    port = 38435
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except Exception:
            pass
    LOG.touch(exist_ok=True)
    prepare_fps_runtime()
    if not OVERLAY_CONFIG_JSON.exists(): write_json(OVERLAY_CONFIG_JSON, default_overlay_config())
    ensure_overlay_process()
    atexit.register(cleanup)
    # launch.sh stops old servers with SIGTERM; turn that into a normal exit so
    # cleanup() runs and the replay-buffer ffmpeg process is stopped too.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    start_cursor_init(False)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=idle_watch, args=(server,), daemon=True).start()
    threading.Thread(target=playtime_watch, daemon=True).start()
    threading.Thread(target=ember_sync_watch, daemon=True).start()
    threading.Thread(target=mods_reapply_watch, daemon=True).start()
    threading.Thread(target=retire_removed_mods, daemon=True).start()
    threading.Thread(target=repair_texture_targets, daemon=True).start()
    global WIN_WATCHER
    WIN_WATCHER = ranks.WinWatcher(win_config, _on_auto_win, game_running, log)
    WIN_WATCHER.start()
    clips.SOUND_VOLUME = clip_sound_volume
    clips.init(log, game_running)
    updater.init(log, APP_VERSION, os.environ.get("PG3D_APP_BUNDLE"), game_running,
                 post_install=bool(os.environ.get("PG3D_POST_INSTALL")))
    log(f"PG3D Mac Client server started on 127.0.0.1:{port}")
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        cleanup()
        server.server_close()


if __name__ == "__main__":
    main()
