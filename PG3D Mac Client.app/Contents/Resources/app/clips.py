"""Replay buffer ("Clips") for PG3D Mac Client.

While PG3D is running, ffmpeg records the screen into a rolling ring of short
MPEG-TS segments. Pressing the clip hotkey (or "Save clip now") copies the most
recent segments, joins them without re-encoding and trims to the chosen length
(10 s / 30 s / 1 min / 3 min). Clips land in ~/Movies/PG3D Clips.
"""
from __future__ import annotations

import json
import math
import queue
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

try:
    import Quartz  # pyobjc-framework-Quartz is installed by launch.sh
except Exception:  # pragma: no cover - only missing outside macOS
    Quartz = None

SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
STATE_DIR = SUPPORT / "state"
BUFFER_DIR = SUPPORT / "replay_buffer"
STAGING_DIR = SUPPORT / "clip_staging"
THUMBS_DIR = SUPPORT / "clip_thumbs"
CLIPS_DIR = Path.home() / "Movies/PG3D Clips"
CONFIG_JSON = STATE_DIR / "clips_config.json"
INDEX_JSON = STATE_DIR / "clips_index.json"
FFMPEG_LOG = SUPPORT / "ffmpeg.log"

FFMPEG_URLS = {
    # Static builds by Martin Riedl (ffmpeg.martin-riedl.de); include AVFoundation capture + VideoToolbox.
    "arm64": "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip",
    "x86_64": "https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffmpeg.zip",
}
STRIP_FRAMES = 10
SEGMENT_SECONDS = 2
LENGTHS = (10, 30, 60, 180)
QUALITIES = {
    # id: (output height or None for native, fps, video bitrate)
    "720p30": (720, 30, "6M"),
    "1080p60": (1080, 60, "12M"),
    "native60": (None, 60, "20M"),
}
CLIP_NAME_RE = re.compile(r"[^/\\\x00]{1,120}\.mp4")

# DOM KeyboardEvent.code -> macOS virtual key code (same table as overlay.py).
KEY_CODES = {
    "KeyA": 0, "KeyS": 1, "KeyD": 2, "KeyF": 3, "KeyH": 4, "KeyG": 5, "KeyZ": 6, "KeyX": 7, "KeyC": 8, "KeyV": 9,
    "KeyB": 11, "KeyQ": 12, "KeyW": 13, "KeyE": 14, "KeyR": 15, "KeyY": 16, "KeyT": 17,
    "Digit1": 18, "Digit2": 19, "Digit3": 20, "Digit4": 21, "Digit6": 22, "Digit5": 23, "Equal": 24, "Digit9": 25,
    "Digit7": 26, "Minus": 27, "Digit8": 28, "Digit0": 29, "BracketRight": 30, "KeyO": 31, "KeyU": 32,
    "BracketLeft": 33, "KeyI": 34, "KeyP": 35, "Enter": 36, "KeyL": 37, "KeyJ": 38, "Quote": 39, "KeyK": 40,
    "Semicolon": 41, "Backslash": 42, "Comma": 43, "Slash": 44, "KeyN": 45, "KeyM": 46, "Period": 47, "Tab": 48,
    "Space": 49, "Backquote": 50, "Backspace": 51, "Escape": 53, "F17": 64, "NumpadDecimal": 65,
    "NumpadMultiply": 67, "NumpadAdd": 69, "NumpadDivide": 75, "NumpadEnter": 76, "NumpadSubtract": 78,
    "F18": 79, "F19": 80, "NumpadEqual": 81, "Numpad0": 82, "Numpad1": 83, "Numpad2": 84, "Numpad3": 85,
    "Numpad4": 86, "Numpad5": 87, "Numpad6": 88, "Numpad7": 89, "F20": 90, "Numpad8": 91, "Numpad9": 92,
    "F5": 96, "F6": 97, "F7": 98, "F3": 99, "F8": 100, "F9": 101, "F11": 103, "F13": 105, "F16": 106,
    "F14": 107, "F10": 109, "F12": 111, "F15": 113, "Home": 115, "PageUp": 116, "Delete": 117, "F4": 118,
    "End": 119, "F2": 120, "PageDown": 121, "F1": 122, "ArrowLeft": 123, "ArrowRight": 124,
    "ArrowDown": 125, "ArrowUp": 126,
}
MOD_CODES = {"shift": (56, 60), "ctrl": (59, 62), "alt": (58, 61), "cmd": (55, 54)}

_log = print  # replaced by server.log via init()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- config

def default_config():
    return {
        "enabled": True,
        "length": 60,
        "quality": "1080p60",
        "screen": None,              # avfoundation device index; None = first screen
        "only_while_playing": True,
        "notify": True,
        "hotkey": {"code": "KeyC", "key": "c", "display": "Ctrl+Shift+C",
                   "mods": {"shift": True, "ctrl": True, "alt": False, "cmd": False}},
    }


def load_config():
    cfg = default_config()
    raw = _read_json(CONFIG_JSON, {})
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if k in cfg})
    return _validate(cfg)


def _validate(cfg, base=None):
    """Clean a config dict; invalid values fall back to `base` (defaults if omitted)."""
    base = base or default_config()
    out = dict(base)
    out["enabled"] = bool(cfg.get("enabled", base["enabled"]))
    out["only_while_playing"] = bool(cfg.get("only_while_playing", base["only_while_playing"]))
    out["notify"] = bool(cfg.get("notify", base["notify"]))
    try:
        length = int(cfg.get("length", base["length"]))
    except Exception:
        length = base["length"]
    out["length"] = length if length in LENGTHS else base["length"]
    out["quality"] = cfg.get("quality") if cfg.get("quality") in QUALITIES else base["quality"]
    screen = cfg.get("screen")
    out["screen"] = int(screen) if isinstance(screen, (int, float, str)) and str(screen).isdigit() else None
    hk = cfg.get("hotkey")
    if isinstance(hk, dict) and str(hk.get("code")) in KEY_CODES:
        mods = hk.get("mods") if isinstance(hk.get("mods"), dict) else {}
        out["hotkey"] = {
            "code": str(hk["code"]), "key": str(hk.get("key") or hk["code"])[:40],
            "display": str(hk.get("display") or hk["code"])[:80],
            "mods": {m: bool(mods.get(m, False)) for m in MOD_CODES},
        }
    return out


# --------------------------------------------------------------------------- ffmpeg helpers

def find_ffmpeg():
    for p in (SUPPORT / "bin/ffmpeg", Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg")):
        if p.is_file():
            return str(p)
    return shutil.which("ffmpeg")


def list_screens(ffmpeg):
    """Return [{index, name}] for avfoundation screen-capture devices."""
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                           capture_output=True, text=True, timeout=8)
    except Exception as e:
        _log(f"Clips: could not list capture devices: {e}")
        return []
    screens = []
    for line in r.stderr.splitlines():
        m = re.search(r"\[(\d+)\]\s+(Capture screen.*)$", line)
        if m:
            screens.append({"index": int(m.group(1)), "name": m.group(2).strip()})
    return screens


def media_duration(ffmpeg, path: Path):
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=15)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        pass
    return None


def screen_permission():
    """True/False when macOS can tell us, None when unknown."""
    if Quartz is None:
        return None
    fn = getattr(Quartz, "CGPreflightScreenCaptureAccess", None)
    if fn is None:
        return None
    try:
        return bool(fn())
    except Exception:
        return None


def request_screen_permission():
    if Quartz is not None:
        fn = getattr(Quartz, "CGRequestScreenCaptureAccess", None)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass
    for url in ("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
                "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_ScreenCapture"):
        try:
            subprocess.Popen(["/usr/bin/open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
        except Exception:
            continue
    return {"ok": True}


# Set by server.py from Settings > Sounds: returns the volume 0..1, or 0 for no sound.
SOUND_VOLUME = lambda: 1.0


def notify(title, text):
    safe = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.Popen(["/usr/bin/osascript", "-e",
                          f'display notification "{safe(text)}" with title "{safe(title)}"'],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    # The macOS "Glass" sound, played separately so the client's volume setting applies.
    try:
        vol = float(SOUND_VOLUME())
    except Exception:
        vol = 1.0
    if vol > 0:
        try:
            subprocess.Popen(["/usr/bin/afplay", "-v", f"{vol:.2f}", "/System/Library/Sounds/Glass.aiff"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


# --------------------------------------------------------------------------- recorder

class Recorder:
    def __init__(self):
        self.lock = threading.RLock()
        self.proc = None
        self.proc_started = 0.0
        self.encoder = "h264_videotoolbox"
        self.phase = "starting"
        self.message = ""
        self.retry_at = 0.0
        self.saving = 0
        self.last_clip = None
        self.last_error = None
        self.install = {"running": False, "message": None}
        self._screens = None
        self._game_running = lambda: False
        self.shutting_down = False
        self.trim_job = None

    # ----- lifecycle
    def ffmpeg_args(self, ffmpeg, cfg):
        height, fps, bitrate = QUALITIES[cfg["quality"]]
        screen = cfg["screen"]
        if screen is None:
            screens = self.screens(ffmpeg)
            screen = screens[0]["index"] if screens else 1
        # Always keep the longest option buffered so changing the clip length never loses footage.
        wrap = math.ceil(max(LENGTHS) / SEGMENT_SECONDS) + 3
        vf = [f"fps={fps}"]
        if height:
            vf.append(f"scale=-2:{height}:flags=fast_bilinear")
        vf.append("format=yuv420p")
        # stdin stays open so we can send "q" for a clean stop.
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "avfoundation", "-capture_cursor", "0", "-framerate", str(fps),
                "-i", f"{screen}:none",
                "-vf", ",".join(vf), "-c:v", self.encoder, "-b:v", bitrate,
                "-g", str(fps * SEGMENT_SECONDS),
                "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})"]
        if self.encoder == "h264_videotoolbox":
            args += ["-realtime", "1"]
        else:
            args += ["-preset", "ultrafast", "-tune", "zerolatency"]
        args += ["-f", "segment", "-segment_time", str(SEGMENT_SECONDS), "-segment_wrap", str(wrap),
                 "-segment_format", "mpegts",
                 str(BUFFER_DIR / "seg%03d.ts")]
        return args

    def screens(self, ffmpeg=None, refresh=False):
        if self._screens is None or refresh:
            ffmpeg = ffmpeg or find_ffmpeg()
            self._screens = list_screens(ffmpeg) if ffmpeg else []
        return self._screens

    @staticmethod
    def kill_orphans():
        """Stop any replay-buffer ffmpeg left behind by a client that didn't exit cleanly."""
        try:
            subprocess.run(["/usr/bin/pkill", "-f", str(BUFFER_DIR / "seg%03d.ts")],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass

    def start(self, ffmpeg, cfg):
        if self.shutting_down:
            return
        self.kill_orphans()
        shutil.rmtree(BUFFER_DIR, ignore_errors=True)
        BUFFER_DIR.mkdir(parents=True, exist_ok=True)
        args = self.ffmpeg_args(ffmpeg, cfg)
        log_f = open(FFMPEG_LOG, "a", encoding="utf-8")
        log_f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] start encoder={self.encoder}\n")
        log_f.flush()
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log_f)
        log_f.close()
        self.proc_started = time.time()
        self.phase, self.message = "recording", "Replay buffer is recording."
        _log(f"Clips: replay buffer started ({self.encoder}, {cfg['quality']}).")

    def stop(self, reason="stopped"):
        with self.lock:
            proc, self.proc = self.proc, None
        if not proc:
            return
        try:
            proc.stdin.write(b"q")
            proc.stdin.flush()
            proc.stdin.close()
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        _log(f"Clips: replay buffer {reason}.")

    def _ffmpeg_tail(self, lines=6):
        try:
            return "\n".join(FFMPEG_LOG.read_text(errors="ignore").strip().splitlines()[-lines:])
        except Exception:
            return ""

    def supervise(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                _log(f"Clips supervisor error: {e}")
            time.sleep(1.0)

    def _tick(self):
        if self.shutting_down:
            return
        cfg = load_config()
        ffmpeg = find_ffmpeg()
        with self.lock:
            if not ffmpeg:
                self.phase, self.message = "no_ffmpeg", "Setting up the recording tools…"
                if cfg["enabled"]:
                    self.install_ffmpeg()
                return
            playing = self._game_running()
            wanted = cfg["enabled"] and (playing or not cfg["only_while_playing"])
            running = self.proc is not None and self.proc.poll() is None

            if self.proc is not None and not running:
                # ffmpeg exited on its own.
                lived = time.time() - self.proc_started
                tail = self._ffmpeg_tail()
                self.proc = None
                if self.encoder == "h264_videotoolbox" and lived < 5:
                    self.encoder = "libx264"
                    _log("Clips: hardware encoder failed, retrying with libx264.")
                    self.retry_at = 0
                else:
                    self.last_error = tail or "ffmpeg stopped unexpectedly."
                    self.retry_at = time.time() + 15
                    self.phase = "error"
                    self.message = "Recording stopped. Check Screen Recording permission, then it retries automatically."
                    _log(f"Clips: ffmpeg exited after {lived:.1f}s: {tail}")
                return

            if wanted and not running:
                if time.time() < self.retry_at:
                    return
                self.start(ffmpeg, cfg)
            elif not wanted and running:
                self.stop("paused")
                self.phase = "idle"
            elif not wanted:
                self.phase = "off" if not cfg["enabled"] else "idle"
                self.message = ("Clips are turned off." if not cfg["enabled"]
                                else "Waiting for Pixel Gun 3D to start.")

    def restart(self):
        """Apply new quality/screen settings."""
        self.stop("restarting for new settings")
        with self.lock:
            self.encoder = "h264_videotoolbox"
            self.retry_at = 0

    # ----- saving
    def buffered_seconds(self):
        segs = [p for p in BUFFER_DIR.glob("seg*.ts") if p.stat().st_size > 0] if BUFFER_DIR.exists() else []
        if not segs or not self.proc:
            return 0
        return min(len(segs) * SEGMENT_SECONDS, int(time.time() - self.proc_started))

    def _segments(self):
        if not BUFFER_DIR.exists():
            return []
        segs = []
        for p in BUFFER_DIR.glob("seg*.ts"):
            try:
                st = p.stat()
            except FileNotFoundError:
                continue
            if st.st_size > 0:
                segs.append((st.st_mtime, p))
        return [p for _, p in sorted(segs)]

    def save_clip(self, length=None):
        cfg = load_config()
        length = int(length or cfg["length"])
        ffmpeg = find_ffmpeg()
        with self.lock:
            if not ffmpeg or self.proc is None or self.proc.poll() is not None:
                raise RuntimeError("The replay buffer isn't recording right now. Start Pixel Gun 3D first.")
            if not self._segments():
                raise RuntimeError("Nothing has been recorded yet. Give it a couple of seconds.")
            self.saving += 1
        threading.Thread(target=self._finish_clip, args=(ffmpeg, length, cfg), daemon=True).start()
        return {"ok": True, "pending": True, "length": length}

    def _finish_clip(self, ffmpeg, length, cfg):
        job = STAGING_DIR / f"job-{int(time.time() * 1000)}"
        try:
            # Wait for the chunk being written right now to close. That keeps the
            # moment you pressed the hotkey in the clip and avoids a torn last frame.
            segs = self._segments()
            in_progress = segs[-1] if segs else None
            deadline = time.time() + SEGMENT_SECONDS + 1.5
            while time.time() < deadline:
                now = self._segments()
                if now and now[-1] != in_progress:
                    break
                time.sleep(0.1)
            complete = self._segments()[:-1]  # newest one is still being written
            # Keyframes are forced on every chunk boundary, so joining exactly
            # ceil(length / 2) chunks gives the requested length with no re-encode.
            chosen = complete[-math.ceil(length / SEGMENT_SECONDS):]
            if not chosen:
                raise RuntimeError("Not enough footage recorded yet.")
            job.mkdir(parents=True, exist_ok=True)
            staged = []
            for i, seg in enumerate(chosen):
                dst = job / f"{i:03d}.ts"
                shutil.copyfile(seg, dst)
                staged.append(dst)

            CLIPS_DIR.mkdir(parents=True, exist_ok=True)
            name = datetime.now().strftime("PG3D %Y-%m-%d %H-%M-%S.mp4")
            out = CLIPS_DIR / name
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                            "-i", "concat:" + "|".join(str(p) for p in staged),
                            "-c", "copy", "-avoid_negative_ts", "make_zero",
                            "-movflags", "+faststart", str(out)],
                           check=True, capture_output=True, timeout=180)
            duration = media_duration(ffmpeg, out) or len(staged) * SEGMENT_SECONDS
            make_thumb(ffmpeg, out, duration)
            make_strip(ffmpeg, out, duration)
            index = _read_json(INDEX_JSON, {})
            index[name] = {"duration": round(duration, 2), "created": int(time.time()), "mtime": out.stat().st_mtime}
            _write_json(INDEX_JSON, index)
            with self.lock:
                self.last_clip = {"name": name, "at": int(time.time())}
                self.last_error = None
            _log(f"Clips: saved {name} ({duration:.1f}s).")
            if cfg.get("notify"):
                notify("Clip saved", f"Last {fmt_len(length)} saved to PG3D Clips.")
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b"").decode(errors="ignore").strip()[-400:]
            _log(f"Clips: saving failed: {err}")
            with self.lock:
                self.last_error = err or "Saving the clip failed."
            if cfg.get("notify"):
                notify("Clip failed", "The clip couldn't be saved. See the Clips tab.")
        except Exception as e:
            _log(f"Clips: saving failed: {e}")
            with self.lock:
                self.last_error = str(e)
        finally:
            shutil.rmtree(job, ignore_errors=True)
            with self.lock:
                self.saving = max(0, self.saving - 1)

    # ----- hotkey
    def hotkey_loop(self):
        was_down = False
        last_fire = 0.0
        hk, hk_loaded = None, 0.0
        while True:
            try:
                if hk is None or time.time() - hk_loaded > 0.5:
                    hk, hk_loaded = load_config()["hotkey"], time.time()
                down = _hotkey_down(hk)
                if down and not was_down and time.time() - last_fire > 1.5:
                    last_fire = time.time()
                    try:
                        self.save_clip()
                    except Exception as e:
                        with self.lock:
                            self.last_error = str(e)
                        if load_config().get("notify"):
                            notify("Couldn't save clip", str(e))
                was_down = down
            except Exception:
                pass
            time.sleep(0.03)

    # ----- ffmpeg install (automatic, no Homebrew needed)
    def install_ffmpeg(self, manual=False):
        with self.lock:
            if self.install["running"]:
                return {"ok": True, "running": True}
            if not manual and time.time() < self.install.get("retry_at", 0):
                return {"ok": False}
            self.install = {"running": True, "message": "Downloading recording tools (about 30 MB)…"}
        threading.Thread(target=self._install_ffmpeg, daemon=True).start()
        return {"ok": True, "running": True}

    def _install_ffmpeg(self):
        tmp = SUPPORT / "ffmpeg-download"
        try:
            try:
                arm = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], capture_output=True,
                                     text=True, timeout=5).stdout.strip() == "1"
            except Exception:
                arm = True
            url = FFMPEG_URLS["arm64" if arm else "x86_64"]
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir(parents=True, exist_ok=True)
            # System curl uses the macOS certificate store and doesn't mark the file as downloaded-from-web.
            subprocess.run(["/usr/bin/curl", "-fL", "--retry", "3", "--connect-timeout", "20", "-o",
                            str(tmp / "ffmpeg.zip"), url], check=True, capture_output=True, timeout=900)
            subprocess.run(["/usr/bin/ditto", "-x", "-k", str(tmp / "ffmpeg.zip"), str(tmp / "x")],
                           check=True, capture_output=True, timeout=120)
            binary = next((p for p in (tmp / "x").rglob("ffmpeg") if p.is_file()), None)
            if not binary:
                raise RuntimeError("the download didn't contain ffmpeg")
            binary.chmod(0o755)
            subprocess.run(["/usr/bin/xattr", "-c", str(binary)], capture_output=True)
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(binary)], capture_output=True)
            devices = subprocess.run([str(binary), "-hide_banner", "-devices"], capture_output=True,
                                     text=True, timeout=20)
            if "avfoundation" not in (devices.stdout + devices.stderr):
                raise RuntimeError("this ffmpeg build can't record the screen")
            dest = SUPPORT / "bin/ffmpeg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(binary), str(dest))
            with self.lock:
                self.install = {"running": False, "message": None}
                self._screens = None
            _log("Clips: recording tools installed.")
        except Exception as e:
            err = e.stderr.decode(errors="ignore")[-200:] if isinstance(e, subprocess.CalledProcessError) and e.stderr else str(e)
            _log(f"Clips: ffmpeg download failed: {err}")
            with self.lock:
                self.install = {"running": False, "failed": True, "retry_at": time.time() + 300,
                                "message": "Couldn't download the recording tools. Check your internet connection."}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ----- trimming
    def trim(self, name, start, end, mode="new"):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError("Recording tools are still being set up.")
        src = safe_clip_path(name)
        if not src.exists():
            raise RuntimeError("That clip no longer exists.")
        duration = media_duration(ffmpeg, src) or 0
        start, end = max(0.0, float(start)), min(float(end), duration or float(end))
        if end - start < 0.5:
            raise RuntimeError("Pick at least half a second to keep.")
        with self.lock:
            if self.trim_job and self.trim_job.get("running"):
                raise RuntimeError("Another clip is still being trimmed.")
            self.trim_job = {"running": True, "name": name, "progress": 0, "mode": mode, "result": None, "error": None}
        threading.Thread(target=self._trim, args=(ffmpeg, src, start, end, mode), daemon=True).start()
        return {"ok": True}

    def _trim(self, ffmpeg, src, start, end, mode):
        length = end - start
        if mode == "replace":
            out = src.with_name("." + src.stem + ".trimming.mp4")
        else:
            base = f"{src.stem} (trimmed)"
            out, n = CLIPS_DIR / f"{base}.mp4", 2
            while out.exists():
                out, n = CLIPS_DIR / f"{base} {n}.mp4", n + 1
        try:
            size = src.stat().st_size
            src_len = media_duration(ffmpeg, src) or length
            kbps = int(min(40000, max(3000, size * 8 / 1000 / max(src_len, 0.1) * 1.15)))
            attempts = [["-c:v", "h264_videotoolbox", "-b:v", f"{kbps}k"],
                        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]]
            last_err = ""
            for enc in attempts:
                # Seeking before -i with re-encoding is frame-accurate.
                args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src),
                        "-t", f"{length:.3f}", "-map", "0:v:0", "-map", "0:a?", *enc, "-pix_fmt", "yuv420p",
                        "-force_key_frames", "expr:gte(t,n_forced*2)",
                        "-c:a", "copy", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(out)]
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for line in proc.stdout:
                    if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                        try:
                            done = int(line.split("=", 1)[1]) / 1e6
                            with self.lock:
                                self.trim_job["progress"] = max(0, min(99, int(done / length * 100)))
                        except ValueError:
                            pass
                proc.wait()
                last_err = proc.stderr.read()[-300:]
                if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                    break
                out.unlink(missing_ok=True)
            else:
                raise RuntimeError(last_err.strip() or "ffmpeg couldn't trim this clip")

            final = src if mode == "replace" else out
            if mode == "replace":
                out.replace(src)
            duration = media_duration(ffmpeg, final) or length
            for suffix in (".jpg", ".strip.jpg"):
                (THUMBS_DIR / (final.stem + suffix)).unlink(missing_ok=True)
            make_thumb(ffmpeg, final, duration)
            make_strip(ffmpeg, final, duration)
            index = _read_json(INDEX_JSON, {})
            index[final.name] = {"duration": round(duration, 2), "created": int(time.time()),
                                 "mtime": final.stat().st_mtime}
            _write_json(INDEX_JSON, index)
            with self.lock:
                self.trim_job.update(running=False, progress=100, result=final.name)
            _log(f"Clips: trimmed {src.name} -> {final.name} ({length:.1f}s)")
        except Exception as e:
            out.unlink(missing_ok=True) if mode == "replace" else None
            _log(f"Clips: trim failed: {e}")
            with self.lock:
                self.trim_job.update(running=False, error=str(e))

    # ----- status
    def status(self):
        with self.lock:
            return {
                "phase": self.phase,
                "message": self.message,
                "encoder": self.encoder,
                "buffered": self.buffered_seconds(),
                "saving": self.saving,
                "last_clip": self.last_clip,
                "last_error": self.last_error,
                "ffmpeg": bool(find_ffmpeg()),
                "install": {k: v for k, v in self.install.items() if k != "retry_at"},
                "trim": dict(self.trim_job) if self.trim_job else None,
                "permission": screen_permission(),
            }


def _key_down(code):
    if Quartz is None:
        return False
    try:
        return bool(Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, int(code)))
    except Exception:
        return False


def _hotkey_down(hk):
    code = KEY_CODES.get(str(hk.get("code")))
    if code is None:
        return False
    mods = hk.get("mods") or {}
    for name, codes in MOD_CODES.items():
        if bool(mods.get(name)) != any(_key_down(c) for c in codes):
            return False
    return _key_down(code)


def fmt_len(seconds):
    seconds = int(seconds)
    return f"{seconds // 60} min" if seconds >= 60 and seconds % 60 == 0 else f"{seconds} s"


def make_thumb(ffmpeg, clip: Path, duration=None):
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    thumb = THUMBS_DIR / (clip.stem + ".jpg")
    at = min(1.0, (duration or 2) / 2)
    try:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.2f}", "-i", str(clip),
                        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(thumb)],
                       capture_output=True, timeout=30)
    except Exception:
        pass
    return thumb


def make_strip(ffmpeg, clip: Path, duration=None):
    """A row of evenly spaced frames, used for the timeline and hover previews."""
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    strip = THUMBS_DIR / (clip.stem + ".strip.jpg")
    duration = duration or media_duration(ffmpeg, clip) or 1
    rate = STRIP_FRAMES / max(duration, 0.5)
    # Long clips: decode keyframes only (every 2 s) instead of every frame; ~20x faster.
    fast = ["-skip_frame", "nokey"] if duration > 30 else []
    try:
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *fast, "-i", str(clip),
                        "-vf", f"fps={rate:.5f},scale=240:-2,tile={STRIP_FRAMES}x1", "-frames:v", "1",
                        "-q:v", "5", str(strip)], capture_output=True, timeout=120)
    except Exception:
        pass
    return strip


_ART_QUEUE = queue.PriorityQueue()
_ART_PENDING = set()


def _art_worker():
    """Thumbnails first (quick), filmstrips after, so the library fills in fast."""
    while True:
        prio, _, clip, duration, kind = _ART_QUEUE.get()
        try:
            ffmpeg = find_ffmpeg()
            if ffmpeg and clip.exists():
                if kind == "thumb" and not (THUMBS_DIR / (clip.stem + ".jpg")).exists():
                    make_thumb(ffmpeg, clip, duration)
                elif kind == "strip" and not (THUMBS_DIR / (clip.stem + ".strip.jpg")).exists():
                    make_strip(ffmpeg, clip, duration)
        except Exception as e:
            _log(f"Clips: preview images failed for {clip.name}: {e}")
        finally:
            _ART_PENDING.discard((clip.name, kind))


_ART_SEQ = [0]


def _queue_art(clip, duration):
    for prio, kind, target in ((0, "thumb", ".jpg"), (1, "strip", ".strip.jpg")):
        if not (THUMBS_DIR / (clip.stem + target)).exists() and (clip.name, kind) not in _ART_PENDING:
            _ART_PENDING.add((clip.name, kind))
            _ART_SEQ[0] += 1
            _ART_QUEUE.put((prio, _ART_SEQ[0], clip, duration, kind))


# --------------------------------------------------------------------------- library

def safe_clip_path(name):
    name = str(name or "")
    if not CLIP_NAME_RE.fullmatch(name) or name.startswith("."):
        raise RuntimeError("Invalid clip name.")
    path = (CLIPS_DIR / name).resolve()
    if path.parent != CLIPS_DIR.resolve():
        raise RuntimeError("Invalid clip name.")
    return path


def list_clips():
    if not CLIPS_DIR.exists():
        return []
    index = _read_json(INDEX_JSON, {})
    ffmpeg = find_ffmpeg()
    changed = False
    out = []
    for p in sorted((c for c in CLIPS_DIR.glob("*.mp4") if not c.name.startswith(".")),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        meta = index.get(p.name) or {}
        if meta.get("mtime") != st.st_mtime and ffmpeg:
            meta = {"duration": round(media_duration(ffmpeg, p) or 0, 2), "created": int(st.st_mtime), "mtime": st.st_mtime}
            index[p.name] = meta
            changed = True
        thumb = THUMBS_DIR / (p.stem + ".jpg")
        strip = THUMBS_DIR / (p.stem + ".strip.jpg")
        if ffmpeg and (not thumb.exists() or not strip.exists()):
            _queue_art(p, meta.get("duration"))
        out.append({
            "name": p.name, "title": p.stem, "size": st.st_size,
            "duration": meta.get("duration") or 0, "created": meta.get("created") or int(st.st_mtime),
            "thumb": thumb.exists(), "strip": strip.exists(), "v": int(st.st_mtime),
        })
    if changed:
        _write_json(INDEX_JSON, index)
    return out


def delete_clip(name):
    path = safe_clip_path(name)
    trashed = False
    try:
        # Move to the Trash so an accidental delete can be undone from Finder.
        from Foundation import NSFileManager, NSURL
        result = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            NSURL.fileURLWithPath_(str(path)), None, None)
        trashed = bool(result[0] if isinstance(result, tuple) else result)
    except Exception:
        trashed = False
    if not trashed:
        path.unlink(missing_ok=True)
    for suffix in (".jpg", ".strip.jpg"):
        (THUMBS_DIR / (path.stem + suffix)).unlink(missing_ok=True)
    return {"ok": True}


def rename_clip(name, new_title):
    path = safe_clip_path(name)
    title = re.sub(r"[/\\:\x00]", "-", str(new_title or "")).strip()[:100]
    if not title:
        raise RuntimeError("Enter a name for the clip.")
    target = safe_clip_path(title + ".mp4")
    if target.exists() and target != path:
        raise RuntimeError("A clip with that name already exists.")
    path.rename(target)
    for suffix in (".jpg", ".strip.jpg"):
        old = THUMBS_DIR / (path.stem + suffix)
        if old.exists():
            old.rename(THUMBS_DIR / (target.stem + suffix))
    index = _read_json(INDEX_JSON, {})
    if name in index:
        index[target.name] = {**index.pop(name), "mtime": target.stat().st_mtime}
        _write_json(INDEX_JSON, index)
    return {"ok": True, "name": target.name}


def reveal_clip(name=None):
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    if name:
        subprocess.Popen(["/usr/bin/open", "-R", str(safe_clip_path(name))])
    else:
        subprocess.Popen(["/usr/bin/open", str(CLIPS_DIR)])
    return {"ok": True}


# --------------------------------------------------------------------------- module API

RECORDER = Recorder()


def init(log_fn, game_running_fn):
    global _log
    _log = log_fn
    RECORDER._game_running = game_running_fn
    for p in (STATE_DIR, BUFFER_DIR, THUMBS_DIR):
        p.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(STAGING_DIR, ignore_errors=True)
    Recorder.kill_orphans()
    threading.Thread(target=RECORDER.supervise, daemon=True).start()
    threading.Thread(target=_art_worker, daemon=True).start()
    threading.Thread(target=RECORDER.hotkey_loop, daemon=True).start()


def shutdown():
    RECORDER.shutting_down = True
    with RECORDER.lock:  # wait out any in-flight supervisor tick
        pass
    RECORDER.stop("shut down")


def payload():
    ffmpeg = find_ffmpeg()
    return {
        "ok": True,
        "config": load_config(),
        "status": RECORDER.status(),
        "screens": RECORDER.screens(ffmpeg) if ffmpeg else [],
        "clips": list_clips(),
        "folder": str(CLIPS_DIR),
        "lengths": list(LENGTHS),
        "qualities": list(QUALITIES),
    }


def set_config(raw):
    old = load_config()
    merged = {**old, **(raw if isinstance(raw, dict) else {})}
    cfg = _validate(merged, base=old)
    _write_json(CONFIG_JSON, cfg)
    if (cfg["quality"], cfg["screen"]) != (old["quality"], old["screen"]):
        RECORDER.restart()
    return {"ok": True, "config": cfg}
