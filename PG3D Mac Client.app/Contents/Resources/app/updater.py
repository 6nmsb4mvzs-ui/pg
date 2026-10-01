"""Self-updating and download cleanup for PG3D Mac Client.

Download a new version (zip or app) into ~/Downloads and the running client
installs it over itself, restarts, and moves the downloaded files to the Trash.
Older copies found in Downloads are cleaned up the same way.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path

APP_NAME = "PG3D Mac Client.app"
BUNDLE_ID_PREFIX = "local.pg3d.macclient"
DOWNLOADS = Path.home() / "Downloads"
SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
STATE_DIR = SUPPORT / "state"
PREFS_JSON = STATE_DIR / "updates.json"
STAGING = SUPPORT / "update-staging"
NAME_RE = re.compile(r"^PG3D[ _]Mac[ _]Client", re.I)
SCAN_SECONDS = 10

_log = print


def parse_version(text):
    nums = [int(x) for x in re.findall(r"\d+", str(text or ""))[:3]]
    return tuple(nums + [0] * (3 - len(nums))) if nums else (0, 0, 0)


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_prefs():
    prefs = {"auto_install": True, "auto_clean": True}
    raw = _read_json(PREFS_JSON, {})
    if isinstance(raw, dict):
        for k in prefs:
            if k in raw:
                prefs[k] = bool(raw[k])
    return prefs


def save_prefs(patch):
    prefs = load_prefs()
    for k in ("auto_install", "auto_clean"):
        if k in (patch or {}):
            prefs[k] = bool(patch[k])
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PREFS_JSON.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    return prefs


def _valid_info(info):
    return (isinstance(info, dict) and str(info.get("CFBundleIdentifier", "")).startswith(BUNDLE_ID_PREFIX)
            and info.get("CFBundleShortVersionString"))


def app_version(app: Path):
    try:
        info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except Exception:
        return None
    return str(info["CFBundleShortVersionString"]) if _valid_info(info) else None


def zip_app(zpath: Path):
    """(version, member prefix of the .app) for a client zip, else None."""
    try:
        with zipfile.ZipFile(zpath) as zf:
            members = [m for m in zf.namelist()
                       if m.endswith(APP_NAME + "/Contents/Info.plist") and m.count("/") <= 4
                       and "__MACOSX" not in m]
            if not members:
                return None
            member = min(members, key=len)
            info = plistlib.loads(zf.read(member))
    except Exception:
        return None
    if not _valid_info(info):
        return None
    return str(info["CFBundleShortVersionString"]), member[: -len("/Contents/Info.plist")]


def trash(path: Path):
    """Move to the Trash (recoverable); fall back to deleting."""
    try:
        from Foundation import NSFileManager, NSURL
        result = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(path)), None, None)
        if bool(result[0] if isinstance(result, tuple) else result):
            return True
    except Exception:
        pass
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except Exception as e:
        _log(f"Updater: couldn't remove {path}: {e}")
        return False


class Updater:
    def __init__(self, current_version, app_bundle, game_running=lambda: False):
        self.current = str(current_version)
        self.app_bundle = Path(app_bundle) if app_bundle else None
        self.game_running = game_running
        self.lock = threading.RLock()
        self.phase = "idle"          # idle | installing | restarting | error | no_access
        self.message = ""
        self.available = None        # {"version", "path", "kind"}
        self.cleaned = []            # names moved to the Trash recently
        self.last_scan = 0.0
        self._zip_cache = {}

    # ----- discovery
    def _candidates(self):
        found = []
        try:
            entries = list(DOWNLOADS.iterdir())
        except PermissionError:
            with self.lock:
                self.phase, self.message = "no_access", "Allow access to Downloads so the client can find new versions."
            return None
        except FileNotFoundError:
            return []
        running = self.app_bundle.resolve() if self.app_bundle and self.app_bundle.exists() else None
        for p in entries:
            name = p.name
            if not NAME_RE.match(name) or name.endswith((".crdownload", ".download", ".part")):
                continue
            try:
                if p.suffix.lower() == ".zip" and p.is_file():
                    st = p.stat()
                    key = (str(p), st.st_mtime, st.st_size)
                    if key not in self._zip_cache:
                        self._zip_cache[key] = zip_app(p)
                    hit = self._zip_cache[key]
                    if hit:
                        found.append({"path": p, "kind": "zip", "version": hit[0], "member": hit[1]})
                elif p.suffix == ".app" and p.is_dir():
                    v = app_version(p)
                    if v and p.resolve() != running:
                        found.append({"path": p, "kind": "app", "version": v, "app": p})
                elif p.is_dir():
                    inner = next((a for a in p.glob("**/" + APP_NAME) if len(a.relative_to(p).parts) <= 2), None)
                    v = app_version(inner) if inner else None
                    if v and inner.resolve() != running:
                        found.append({"path": p, "kind": "folder", "version": v, "app": inner})
            except Exception as e:
                _log(f"Updater: skipped {name}: {e}")
        return found

    def scan(self):
        cands = self._candidates()
        access_ok = cands is not None
        cands = cands or []
        cur = parse_version(self.current)
        newer = [c for c in cands if parse_version(c["version"]) > cur]
        stale = [c for c in cands if parse_version(c["version"]) <= cur]
        best = max(newer, key=lambda c: parse_version(c["version"])) if newer else None
        with self.lock:
            self.last_scan = time.time()
            self.available = ({"version": best["version"], "path": str(best["path"]), "kind": best["kind"]}
                              if best else None)
            if self.phase == "no_access" and access_ok:
                self.phase, self.message = "idle", ""
        return best, stale, newer

    # ----- cleanup
    def clean(self, items):
        names = []
        for c in items:
            p = Path(c["path"])
            if p.exists() and trash(p):
                names.append(p.name)
        if names:
            _log("Updater: moved to Trash: " + ", ".join(names))
            with self.lock:
                self.cleaned = (names + self.cleaned)[:20]
        return names

    # ----- install
    def install(self, cand=None):
        best, stale, newer = self.scan()
        cand = cand or best
        if not cand:
            raise RuntimeError("No newer version was found in Downloads.")
        with self.lock:
            if self.phase in ("installing", "restarting"):
                return {"ok": True, "phase": self.phase}
            self.phase, self.message = "installing", f"Installing version {cand['version']}…"
        threading.Thread(target=self._install, args=(cand, stale + [c for c in newer if c is not cand]),
                         daemon=True).start()
        return {"ok": True, "phase": "installing", "version": cand["version"]}

    def _install(self, cand, leftovers):
        try:
            shutil.rmtree(STAGING, ignore_errors=True)
            STAGING.mkdir(parents=True, exist_ok=True)
            if cand["kind"] == "zip":
                subprocess.run(["/usr/bin/ditto", "-x", "-k", str(cand["path"]), str(STAGING / "x")],
                               check=True, capture_output=True, timeout=300)
                staged = STAGING / "x" / cand["member"]
            else:
                staged = STAGING / APP_NAME
                subprocess.run(["/usr/bin/ditto", str(cand["app"]), str(staged)],
                               check=True, capture_output=True, timeout=300)
            if app_version(staged) != cand["version"]:
                raise RuntimeError("The downloaded version looks incomplete.")
            subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", str(staged)], capture_output=True)

            dest = self.app_bundle
            if not dest or not str(dest).endswith(".app") or "/AppTranslocation/" in str(dest):
                dest = Path("/Applications") / APP_NAME
            previous = dest.with_name(dest.name + ".previous")
            shutil.rmtree(previous, ignore_errors=True)
            if dest.exists():
                dest.rename(previous)
            try:
                shutil.move(str(staged), str(dest))
            except Exception:
                if previous.exists() and not dest.exists():
                    previous.rename(dest)
                raise
            shutil.rmtree(previous, ignore_errors=True)
            shutil.rmtree(STAGING, ignore_errors=True)
            _log(f"Updater: installed {cand['version']} to {dest}")

            self.clean([cand] + leftovers)
            with self.lock:
                self.phase, self.message = "restarting", f"Restarting into version {cand['version']}…"
            time.sleep(0.8)
            # The new launcher stops this copy and takes over.
            subprocess.Popen(["/usr/bin/open", "-n", str(dest)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            _log(f"Updater: install failed: {e}")
            shutil.rmtree(STAGING, ignore_errors=True)
            with self.lock:
                self.phase, self.message = "error", f"Couldn't install the update: {e}"

    # ----- background
    def watch(self, post_install=False):
        if post_install:
            try:
                _, stale, _ = self.scan()
                self.clean(stale)
            except Exception as e:
                _log(f"Updater: post-install cleanup failed: {e}")
        while True:
            try:
                prefs = load_prefs()
                best, stale, _ = self.scan()
                if stale and prefs["auto_clean"]:
                    self.clean(stale)
                if best and prefs["auto_install"] and self.phase not in ("installing", "restarting", "error"):
                    if self.game_running():
                        with self.lock:
                            self.message = f"Version {best['version']} will install when you close Pixel Gun 3D."
                    else:
                        self.install(best)
            except Exception as e:
                _log(f"Updater: scan failed: {e}")
            time.sleep(SCAN_SECONDS)

    def state(self):
        with self.lock:
            return {
                "current": self.current,
                "available": self.available,
                "phase": self.phase,
                "message": self.message,
                "cleaned": list(self.cleaned),
                "last_scan": int(self.last_scan),
                "installed_at": str(self.app_bundle) if self.app_bundle else None,
                "prefs": load_prefs(),
            }


def cleanup_support(current_version):
    """Remove leftovers from older versions inside Application Support."""
    removed = []
    fps_root = SUPPORT / "fps_runtime"
    if fps_root.is_dir():
        for d in fps_root.iterdir():
            if d.is_dir() and d.name != current_version:
                shutil.rmtree(d, ignore_errors=True)
                removed.append(f"fps_runtime/{d.name}")
    for name in ("update-staging", "python-download", "clip_staging"):
        p = SUPPORT / name
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    for pid in list(SUPPORT.glob("*-v*.pid")):
        if f"-v{current_version}." not in pid.name:
            try:
                os.kill(int(pid.read_text().strip() or 0), 0)
            except Exception:
                pid.unlink(missing_ok=True)
    for logname in ("client.log", "ffmpeg.log"):
        p = SUPPORT / logname
        try:
            if p.exists() and p.stat().st_size > 5_000_000:
                data = p.read_bytes()[-1_000_000:]
                p.write_bytes(data)
        except Exception:
            pass
    return removed


UPDATER = None


def init(log_fn, current_version, app_bundle, game_running, post_install=False):
    global _log, UPDATER
    _log = log_fn
    UPDATER = Updater(current_version, app_bundle, game_running)
    try:
        removed = cleanup_support(current_version)
        if removed:
            _log("Updater: removed old files: " + ", ".join(removed))
    except Exception as e:
        _log(f"Updater: support cleanup failed: {e}")
    threading.Thread(target=UPDATER.watch, args=(post_install,), daemon=True).start()
    return UPDATER
