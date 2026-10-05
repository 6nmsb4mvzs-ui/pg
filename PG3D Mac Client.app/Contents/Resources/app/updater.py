"""Self-updating and download cleanup for PG3D Mac Client.

New versions come from two places:
- GitHub: every version is published as a release of the private repo below.
  With a read-only access token saved in Settings, the client checks the
  latest release, downloads its zip and installs it.
- Downloads: a client zip or app dropped into ~/Downloads is installed too.
Either way the client installs over itself, restarts, and moves the
downloaded files to the Trash.
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
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

APP_NAME = "PG3D Mac Client.app"
BUNDLE_ID_PREFIX = "local.pg3d.macclient"
DOWNLOADS = Path.home() / "Downloads"
SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
STATE_DIR = SUPPORT / "state"
PREFS_JSON = STATE_DIR / "updates.json"
STAGING = SUPPORT / "update-staging"
NAME_RE = re.compile(r"^PG3D[ _]Mac[ _]Client", re.I)
SCAN_SECONDS = 10
GITHUB_REPO = "6nmsb4mvzs-ui/pg"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_EVERY = 30 * 60
GITHUB_DIR = SUPPORT / "update-downloads"
KEYCHAIN_SERVICE = "PG3D Mac Client GitHub"
TOKEN_FILE = STATE_DIR / ".github_token"

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
    # use_downloads: look in ~/Downloads for client zips. Off by default: updates come
    # from GitHub, and reading Downloads makes macOS ask for access.
    prefs = {"auto_install": True, "auto_clean": True, "use_downloads": False}
    raw = _read_json(PREFS_JSON, {})
    if isinstance(raw, dict):
        for k in prefs:
            if k in raw:
                prefs[k] = bool(raw[k])
    return prefs


def save_prefs(patch):
    prefs = load_prefs()
    for k in ("auto_install", "auto_clean", "use_downloads"):
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


# ----------------------------------------------------------------- GitHub
def github_token():
    try:
        r = subprocess.run(["/usr/bin/security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", "github", "-w"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


def set_github_token(token):
    token = str(token or "").strip()
    if not token:
        try:
            subprocess.run(["/usr/bin/security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", "github"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        TOKEN_FILE.unlink(missing_ok=True)
        return
    if not re.fullmatch(r"[A-Za-z0-9_]{20,255}", token):
        raise RuntimeError("That doesn't look like a GitHub token.")
    saved = False
    try:
        r = subprocess.run(["/usr/bin/security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", "github",
                            "-w", token], capture_output=True, timeout=10)
        saved = r.returncode == 0
    except Exception:
        saved = False
    if saved:
        TOKEN_FILE.unlink(missing_ok=True)
    else:  # no Keychain access: private file readable only by you
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token, encoding="utf-8")
        os.chmod(TOKEN_FILE, 0o600)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _gh_request(url, token, accept="application/vnd.github+json"):
    req = Request(url, headers={"Accept": accept, "Authorization": f"Bearer {token}",
                                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "PG3D-Mac-Client"})
    return req


def github_latest(token):
    """{"version", "tag", "asset_id", "asset_name", "size"} for the newest release, or None."""
    try:
        with build_opener().open(_gh_request(f"{GITHUB_API}/releases/latest", token), timeout=15) as r:
            rel = json.loads(r.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 404:
            return None  # no release yet (or the token can't see the repo)
        if e.code in (401, 403):
            raise RuntimeError("GitHub didn't accept the token. Check it in Settings › Updates.")
        raise
    asset = next((a for a in rel.get("assets", []) if NAME_RE.match(a.get("name", "")) and a["name"].endswith(".zip")), None)
    if not asset:
        return None
    return {"version": str(rel.get("tag_name", "")).lstrip("v"), "tag": rel.get("tag_name"),
            "asset_id": asset["id"], "asset_name": asset["name"], "size": asset.get("size", 0)}


def github_main(token):
    """{"version", "sha"} of the client on the main branch, or None. Used when the release
    for a new version is late (GitHub sometimes leaves the release job queued for hours)."""
    path = quote(f"{APP_NAME}/Contents/Info.plist")
    try:
        with build_opener().open(_gh_request(f"{GITHUB_API}/contents/{path}?ref=main", token,
                                             "application/vnd.github.raw"), timeout=15) as r:
            info = plistlib.loads(r.read())
        with build_opener().open(_gh_request(f"{GITHUB_API}/commits/main", token,
                                             "application/vnd.github.sha"), timeout=15) as r:
            sha = r.read().decode("utf-8", "ignore").strip()
    except HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("GitHub didn't accept the token. Check it in Settings › Updates.")
        return None
    if not _valid_info(info) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        return None
    v = str(info["CFBundleShortVersionString"])
    return {"version": v, "sha": sha, "zipball": f"{GITHUB_API}/zipball/{sha}",
            "asset_name": f"PG3D_Mac_Client_v{v}_main.zip", "size": 0}


def github_download(token, rel, progress=None):
    """Download a release zip (or the main branch's zipball). GitHub answers with a
    redirect to signed storage, which must be fetched without the token."""
    url = rel.get("zipball") or f"{GITHUB_API}/releases/assets/{rel['asset_id']}"
    opener = build_opener(_NoRedirect)
    try:
        resp = opener.open(_gh_request(url, token, "application/octet-stream"), timeout=30)
        location = None
    except HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        location, resp = e.headers.get("Location"), None
    if location:
        resp = build_opener().open(Request(location, headers={"User-Agent": "PG3D-Mac-Client"}), timeout=60)
    GITHUB_DIR.mkdir(parents=True, exist_ok=True)
    name = Path(rel["asset_name"]).name
    dest, tmp = GITHUB_DIR / name, GITHUB_DIR / (name + ".part")
    total, done = int(rel.get("size") or 0), 0
    with resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    os.replace(tmp, dest)
    return dest


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
        self.github = {"connected": bool(github_token()), "checked": 0, "latest": None, "error": None}
        self._gh_cand = None

    # ----- discovery
    def _candidates(self):
        found = []
        if not load_prefs()["use_downloads"]:
            return found
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
        cands = (cands or []) + ([self._gh_cand] if self._gh_cand and Path(self._gh_cand["path"]).exists() else [])
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

    # ----- GitHub
    def check_github(self, force=False):
        token = github_token()
        with self.lock:
            self.github["connected"] = bool(token)
            if not token:
                self.github.update(latest=None, error=None)
                return None
            if not force and time.time() - self.github["checked"] < GITHUB_EVERY:
                return self.github["latest"]
            self.github["checked"] = time.time()
        try:
            rel = github_latest(token)
            try:
                main = github_main(token)
            except (URLError, OSError, ValueError) as e:
                main = None
                _log(f"Updater: main branch check failed: {e}")
            if main and parse_version(main["version"]) > parse_version(rel["version"] if rel else "0"):
                # The release for this version hasn't been published (GitHub's release job can
                # sit in its queue for hours): take the same version straight from main.
                _log(f"Updater: release {main['version']} not published yet; using the main branch")
                rel = main
            with self.lock:
                self.github.update(latest=rel["version"] if rel else None, error=None)
            if rel and parse_version(rel["version"]) > parse_version(self.current):
                have = self._gh_cand and self._gh_cand["version"] == rel["version"] and Path(self._gh_cand["path"]).exists()
                if not have:
                    with self.lock:
                        self.message = f"Downloading version {rel['version']}…"
                    path = github_download(token, rel)
                    hit = zip_app(path)
                    if not hit or hit[0] != rel["version"]:
                        path.unlink(missing_ok=True)
                        raise RuntimeError("The downloaded release isn't a valid client zip.")
                    self._gh_cand = {"path": path, "kind": "zip", "version": hit[0], "member": hit[1]}
                    with self.lock:
                        self.message = ""
                    _log(f"Updater: downloaded {rel['version']} from GitHub")
            return rel
        except (URLError, OSError, RuntimeError, ValueError) as e:
            msg = str(getattr(e, "reason", None) or e)
            with self.lock:
                self.github["error"] = msg
                if self.message.startswith("Downloading"):
                    self.message = ""
            _log(f"Updater: GitHub check failed: {msg}")
            return None

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
            signer = Path(__file__).resolve().parent / "sign_app.sh"  # same identity => macOS keeps permissions
            if signer.exists():
                r = subprocess.run(["/bin/zsh", str(signer), str(staged)], capture_output=True, text=True, timeout=120)
                _log(f"Updater: {(r.stdout or r.stderr).strip()[-200:]}")

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
            shutil.rmtree(GITHUB_DIR, ignore_errors=True)
            self._gh_cand = None
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
                self.check_github()
                best, stale, _ = self.scan()
                stale = [c for c in stale if Path(c["path"]).parent != GITHUB_DIR]
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
                "github": {k: self.github[k] for k in ("connected", "latest", "error")} |
                          {"checked": int(self.github["checked"]), "repo": GITHUB_REPO},
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
    for name in ("update-staging", "python-download", "clip_staging", "update-downloads"):
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
