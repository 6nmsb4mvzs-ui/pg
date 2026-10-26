"""Idle Slayer game assists — Bonus Stage runner and Chest Hunt solver.

Uses Quartz for input and screen pixel reading. These run as background
threads alongside the macro clicker and can be toggled independently.
"""

import subprocess
import threading
import time
import logging
from typing import Optional, Tuple, Set

logger = logging.getLogger(__name__)

try:
    import Quartz
    from Quartz import (
        CGEventCreateKeyboardEvent, CGEventCreateMouseEvent,
        CGEventPost, kCGHIDEventTap,
        kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID, CGEventCreate, CGEventGetLocation,
    )
    HAS_QUARTZ = True
except ImportError:
    HAS_QUARTZ = False


# ---------------------------------------------------------------------------
# Low-level input helpers
# ---------------------------------------------------------------------------

KEYCODES = {
    "up": 126, "down": 125, "left": 123, "right": 124,
    "space": 49, "return": 36, "escape": 53,
}


def _mouse_pos():
    if not HAS_QUARTZ:
        return (0, 0)
    ev = CGEventCreate(None)
    loc = CGEventGetLocation(ev)
    return (loc.x, loc.y)


def _click(x: int, y: int):
    if not HAS_QUARTZ:
        return
    point = Quartz.CGPointMake(x, y)
    down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, point, 0)
    up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, point, 0)
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)


def _key(name: str):
    if not HAS_QUARTZ:
        return
    code = KEYCODES.get(name)
    if code is None:
        return
    down = CGEventCreateKeyboardEvent(None, code, True)
    up = CGEventCreateKeyboardEvent(None, code, False)
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)


def _pixel_color(x: int, y: int) -> Optional[Tuple[int, int, int]]:
    """Read pixel colour at screen coordinate via CGImage."""
    if not HAS_QUARTZ:
        return None
    try:
        img = Quartz.CGWindowListCreateImage(
            Quartz.CGRectMake(x, y, 1, 1),
            kCGWindowListOptionOnScreenOnly,
            kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
        if img is None:
            return None
        provider = Quartz.CGImageGetDataProvider(img)
        data = Quartz.CGDataProviderCopyData(provider)
        # BGRA on macOS
        return (data[2], data[1], data[0])
    except Exception:
        return None


def _color_match(actual, target, tolerance=30):
    if actual is None:
        return False
    return all(abs(actual[i] - target[i]) <= tolerance for i in range(3))


def _activate_game():
    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "Idle Slayer" to activate'],
            capture_output=True, timeout=2,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bonus Stage Runner
# ---------------------------------------------------------------------------

class BonusStageRunner:
    """Detects when a bonus stage appears and auto-runs it.

    Detection: a bright yellow banner (255, 231, 55) near the top-centre of
    the game window signals a bonus stage prompt.  The runner then presses
    right + up in a loop to clear the stage.
    """

    BANNER_POS = (660, 254)
    BANNER_COLOR = (255, 231, 55)
    RUN_DURATION = 12  # seconds of auto-run per stage

    def __init__(self):
        self.enabled = False
        self.running = False
        self.stages_cleared = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self.running = False

    def _loop(self):
        self.running = True
        while not self._stop.is_set():
            if self.enabled and self._detect_stage():
                self._run_stage()
            self._stop.wait(2)
        self.running = False

    def _detect_stage(self) -> bool:
        px = _pixel_color(*self.BANNER_POS)
        return _color_match(px, self.BANNER_COLOR, tolerance=15)

    def _run_stage(self):
        logger.info("Bonus stage detected — auto-running")
        _click(*self.BANNER_POS)
        time.sleep(0.5)

        end = time.time() + self.RUN_DURATION
        while time.time() < end and not self._stop.is_set():
            _key("right")
            time.sleep(0.08)
            _key("up")
            time.sleep(0.15)

        self.stages_cleared += 1
        logger.info(f"Bonus stage cleared (total: {self.stages_cleared})")

    def to_dict(self):
        return {
            "enabled": self.enabled,
            "running": self.running,
            "stages_cleared": self.stages_cleared,
        }


# ---------------------------------------------------------------------------
# Chest Hunt Solver
# ---------------------------------------------------------------------------

class ChestHuntSolver:
    """Solves the 3x3 Chest Hunt minigame.

    Strategy:
    - Opens chests one by one, tracking which are mimics/guardians.
    - If a guardian is hit, watches an ad for a safety reveal.
    - Prioritises unknown chests with a simple scoring heuristic.
    """

    GRID = [
        (200, 250), (400, 250), (600, 250),
        (200, 400), (400, 400), (600, 400),
        (200, 550), (400, 550), (600, 550),
    ]
    OPENED_COLOR = (51, 51, 51)
    GUARDIAN_COLOR = (255, 0, 0)
    AD_BUTTON_COLOR = (100, 200, 255)
    AD_POSITIONS = [(1200, 680), (1000, 680), (600, 680)]
    # Chest hunt is detected by the guardian-style dark overlay + grid
    HUNT_DETECT_POS = (400, 250)
    HUNT_DETECT_COLOR = (200, 150, 100)  # closed chest brown

    def __init__(self):
        self.enabled = False
        self.running = False
        self.hunts_solved = 0
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self.running = False

    def _loop(self):
        self.running = True
        while not self._stop.is_set():
            if self.enabled and self._detect_hunt():
                self._solve()
            self._stop.wait(2)
        self.running = False

    def _detect_hunt(self) -> bool:
        px = _pixel_color(*self.HUNT_DETECT_POS)
        return _color_match(px, self.HUNT_DETECT_COLOR, tolerance=40)

    def _opened_set(self) -> Set[int]:
        opened = set()
        for i, (x, y) in enumerate(self.GRID):
            px = _pixel_color(x, y)
            if _color_match(px, self.OPENED_COLOR, tolerance=35):
                opened.add(i)
        return opened

    def _guardian_triggered(self) -> bool:
        px = _pixel_color(640, 360)
        return _color_match(px, self.GUARDIAN_COLOR, tolerance=25)

    def _try_watch_ad(self) -> bool:
        for ax, ay in self.AD_POSITIONS:
            px = _pixel_color(ax, ay)
            if _color_match(px, self.AD_BUTTON_COLOR, tolerance=40):
                _click(ax, ay)
                time.sleep(2)
                return True
        return False

    def _choose_next(self, opened: Set[int]) -> Optional[int]:
        available = [i for i in range(9) if i not in opened]
        if not available:
            return None
        # prefer centre, then corners, then edges
        priority = [4, 0, 2, 6, 8, 1, 3, 5, 7]
        for idx in priority:
            if idx in available:
                return idx
        return available[0]

    def _solve(self):
        logger.info("Chest Hunt detected — solving")
        ads_used = 0

        for _ in range(12):
            if self._stop.is_set():
                return
            opened = self._opened_set()
            if len(opened) >= 9:
                break

            nxt = self._choose_next(opened)
            if nxt is None:
                break

            x, y = self.GRID[nxt]
            _click(x, y)
            time.sleep(0.5)

            if self._guardian_triggered() and ads_used < 3:
                self._try_watch_ad()
                ads_used += 1

            time.sleep(0.3)

        self.hunts_solved += 1
        logger.info(f"Chest Hunt solved (total: {self.hunts_solved})")

    def to_dict(self):
        return {
            "enabled": self.enabled,
            "running": self.running,
            "hunts_solved": self.hunts_solved,
        }


# ---------------------------------------------------------------------------
# Combined manager
# ---------------------------------------------------------------------------

class GameAssistManager:
    def __init__(self):
        self.bonus = BonusStageRunner()
        self.chest = ChestHuntSolver()
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self.bonus.start()
        self.chest.start()

    def stop(self):
        self._running = False
        self.bonus.stop()
        self.chest.stop()

    def set_bonus_enabled(self, on: bool):
        self.bonus.enabled = on
        if on and not self._running:
            self.start()

    def set_chest_enabled(self, on: bool):
        self.chest.enabled = on
        if on and not self._running:
            self.start()

    def get_state(self):
        return {
            "bonus_stage": self.bonus.to_dict(),
            "chest_hunt": self.chest.to_dict(),
        }
