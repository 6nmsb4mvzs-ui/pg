"""Macro clicking engine.

Each macro binds a key (keyboard or mouse button) to an auto-repeat click
at a configurable interval. A global toggle key starts/stops all macros.
Uses Quartz (CoreGraphics) for input events on macOS.
"""

import threading
import time
import json
from pathlib import Path
from typing import Optional

try:
    import Quartz
    from Quartz import (
        CGEventCreateKeyboardEvent, CGEventCreateMouseEvent,
        CGEventPost, kCGHIDEventTap,
        kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        kCGEventRightMouseDown, kCGEventRightMouseUp,
        kCGEventOtherMouseDown, kCGEventOtherMouseUp,
        CGEventGetLocation, CGEventCreate,
        kCGEventMouseMoved,
    )
    HAS_QUARTZ = True
except ImportError:
    HAS_QUARTZ = False

CONFIG_NAME = "macros.json"


# Key name → virtual keycode (macOS)
KEYCODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
    "i": 34, "p": 35, "return": 36, "l": 37, "j": 38, "k": 40,
    "n": 45, "m": 46, "tab": 48, "space": 49,
    "escape": 53, "delete": 51,
    "up": 126, "down": 125, "left": 123, "right": 124,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}

MOUSE_BUTTONS = {"mouse1", "mouse2", "mouse3", "mouse4", "mouse5"}


def _current_mouse_pos():
    if not HAS_QUARTZ:
        return (0, 0)
    event = CGEventCreate(None)
    loc = CGEventGetLocation(event)
    return (loc.x, loc.y)


def _click_mouse(button: str):
    if not HAS_QUARTZ:
        return
    x, y = _current_mouse_pos()
    point = Quartz.CGPointMake(x, y)

    if button == "mouse1":
        down_type, up_type = kCGEventLeftMouseDown, kCGEventLeftMouseUp
        btn = 0
    elif button == "mouse2":
        down_type, up_type = kCGEventRightMouseDown, kCGEventRightMouseUp
        btn = 1
    else:
        down_type, up_type = kCGEventOtherMouseDown, kCGEventOtherMouseUp
        btn = int(button.replace("mouse", "")) - 1

    down = CGEventCreateMouseEvent(None, down_type, point, btn)
    up = CGEventCreateMouseEvent(None, up_type, point, btn)
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)


def _press_key(key_name: str):
    if not HAS_QUARTZ:
        return
    keycode = KEYCODES.get(key_name.lower())
    if keycode is None:
        return
    down = CGEventCreateKeyboardEvent(None, keycode, True)
    up = CGEventCreateKeyboardEvent(None, keycode, False)
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)


class Macro:
    def __init__(self, macro_id: str, key: str, interval_ms: int, enabled: bool = False):
        self.id = macro_id
        self.key = key.lower()
        self.interval_ms = max(10, interval_ms)
        self.enabled = enabled
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def to_dict(self):
        return {
            "id": self.id,
            "key": self.key,
            "interval_ms": self.interval_ms,
            "enabled": self.enabled,
            "running": self._thread is not None and self._thread.is_alive(),
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def _loop(self):
        interval = self.interval_ms / 1000.0
        while not self._stop_event.is_set():
            if self.key in MOUSE_BUTTONS:
                _click_mouse(self.key)
            else:
                _press_key(self.key)
            self._stop_event.wait(interval)


class MacroEngine:
    def __init__(self, support_dir: Optional[str] = None):
        if support_dir:
            self._config_path = Path(support_dir) / CONFIG_NAME
        else:
            self._config_path = Path.home() / ".macroclicker" / CONFIG_NAME
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        self.macros: dict[str, Macro] = {}
        self.global_active = False
        self.toggle_key = "f6"
        self._next_id = 1
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not self._config_path.exists():
            return
        try:
            data = json.loads(self._config_path.read_text())
            self.toggle_key = data.get("toggle_key", "f6")
            for m in data.get("macros", []):
                macro = Macro(m["id"], m["key"], m["interval_ms"], m.get("enabled", False))
                self.macros[macro.id] = macro
                num = int(macro.id.replace("macro_", ""), 10) if macro.id.startswith("macro_") else 0
                if num >= self._next_id:
                    self._next_id = num + 1
        except Exception:
            pass

    def _save(self):
        data = {
            "toggle_key": self.toggle_key,
            "macros": [m.to_dict() for m in self.macros.values()],
        }
        self._config_path.write_text(json.dumps(data, indent=2))

    def add_macro(self, key: str, interval_ms: int) -> Macro:
        with self._lock:
            macro_id = f"macro_{self._next_id}"
            self._next_id += 1
            macro = Macro(macro_id, key, interval_ms, enabled=True)
            self.macros[macro_id] = macro
            if self.global_active:
                macro.start()
            self._save()
            return macro

    def remove_macro(self, macro_id: str):
        with self._lock:
            macro = self.macros.pop(macro_id, None)
            if macro:
                macro.stop()
            self._save()

    def update_macro(self, macro_id: str, key: Optional[str] = None,
                     interval_ms: Optional[int] = None, enabled: Optional[bool] = None):
        with self._lock:
            macro = self.macros.get(macro_id)
            if not macro:
                return
            was_running = macro._thread and macro._thread.is_alive()
            if was_running:
                macro.stop()
            if key is not None:
                macro.key = key.lower()
            if interval_ms is not None:
                macro.interval_ms = max(10, interval_ms)
            if enabled is not None:
                macro.enabled = enabled
            if self.global_active and macro.enabled:
                macro.start()
            self._save()

    def toggle_macro(self, macro_id: str):
        with self._lock:
            macro = self.macros.get(macro_id)
            if not macro:
                return
            macro.enabled = not macro.enabled
            if self.global_active and macro.enabled:
                macro.start()
            elif not macro.enabled:
                macro.stop()
            self._save()

    def start_all(self):
        with self._lock:
            self.global_active = True
            for m in self.macros.values():
                if m.enabled:
                    m.start()

    def stop_all(self):
        with self._lock:
            self.global_active = False
            for m in self.macros.values():
                m.stop()

    def set_toggle_key(self, key: str):
        with self._lock:
            self.toggle_key = key.lower()
            self._save()

    def get_state(self):
        return {
            "global_active": self.global_active,
            "toggle_key": self.toggle_key,
            "macros": [m.to_dict() for m in self.macros.values()],
        }
