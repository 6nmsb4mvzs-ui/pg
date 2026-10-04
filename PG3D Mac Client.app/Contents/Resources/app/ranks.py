"""Client rank: XP from PG3D playtime and wins, mapped onto nine tiers.

Ranks are deliberately slow. A typical hour of play (60 minutes plus about four
wins) is worth ~4,600 XP, and the tier thresholds below are set in those
"typical hours": Diamond needs ~130 h, Master ~250 h, Challenger ~750 h. The
top three tiers (and Diamond) also need a minimum number of wins, so idling in
a lobby can't carry you there.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

XP_PER_MINUTE = 60
XP_PER_WIN = 250
TYPICAL_XP_PER_HOUR = XP_PER_MINUTE * 60 + 4 * XP_PER_WIN  # 4,600

# id, name, typical hours to reach, divisions (1 = tier only), wins required to enter
TIERS = [
    ("iron", "Iron", 0, 1, 0),
    ("bronze", "Bronze", 5, 1, 0),
    ("silver", "Silver", 15, 1, 0),
    ("gold", "Gold", 35, 1, 50),
    ("platinum", "Platinum", 70, 1, 150),
    ("diamond", "Diamond", 130, 1, 300),
    ("master", "Master", 250, 1, 800),
    ("grandmaster", "Grandmaster", 450, 1, 1600),
    ("challenger", "Challenger", 750, 1, 3000),
]
DIVISIONS = ["IV", "III", "II", "I"]
TIER_COLORS = {
    "iron": "#8B7F7B", "bronze": "#C07A5E", "silver": "#A9B8CC", "gold": "#E1B565",
    "platinum": "#4FC9B0", "diamond": "#6E8CFF", "master": "#C056F0",
    "grandmaster": "#F0473A", "challenger": "#7FD6FF",
}


def _steps():
    """Flatten tiers into rank steps: (tier index, division label, start xp)."""
    steps = []
    for ti, (tid, name, hours, divs, wins) in enumerate(TIERS):
        start = hours * TYPICAL_XP_PER_HOUR
        if ti + 1 < len(TIERS):
            end = TIERS[ti + 1][2] * TYPICAL_XP_PER_HOUR
        else:
            end = start
        for d in range(divs):
            label = DIVISIONS[d] if divs == 4 else ""
            steps.append((ti, label, int(round(start + (end - start) * d / divs))))
    return steps


STEPS = _steps()
CHALLENGER_XP = STEPS[-1][2]
LP_PER_POINT = 1000  # past Challenger, every 1,000 XP is one point of "LP"


def xp_for(minutes: float, wins: int) -> int:
    return int(max(0, minutes) * XP_PER_MINUTE + max(0, wins) * XP_PER_WIN)


def rank_info(xp: int, wins: int) -> dict:
    """Where `xp` and `wins` place a player, plus progress to the next step."""
    reached = 0
    gated = None
    for i, (ti, _label, start) in enumerate(STEPS):
        if xp < start:
            break
        need = TIERS[ti][4]
        if wins < need:
            gated = {"tier": TIERS[ti][1], "wins_needed": need, "wins_missing": need - wins}
            break
        reached = i
    ti, label, start = STEPS[reached]
    tid, tname = TIERS[ti][0], TIERS[ti][1]
    nxt = STEPS[reached + 1] if reached + 1 < len(STEPS) else None
    if nxt:
        span = max(1, nxt[2] - start)
        progress = max(0.0, min(1.0, (xp - start) / span))
        next_name = f"{TIERS[nxt[0]][1]} {nxt[1]}".strip()
        xp_to_next = max(0, nxt[2] - xp)
    else:
        progress, next_name, xp_to_next = 1.0, None, 0
    return {
        "step": reached,
        "steps_total": len(STEPS),
        "tier": tid,
        "tier_name": tname,
        "division": label,
        "title": f"{tname} {label}".strip(),
        "color": TIER_COLORS[tid],
        "xp": int(xp),
        "step_start": start,
        "next_xp": nxt[2] if nxt else None,
        "next_name": next_name,
        "xp_to_next": xp_to_next,
        "hours_to_next": round(xp_to_next / TYPICAL_XP_PER_HOUR, 1),
        "progress": progress,
        "gated": gated,
        "lp": max(0, (xp - CHALLENGER_XP) // LP_PER_POINT) if tid == "challenger" else None,
    }


def ladder() -> list:
    out = []
    for tid, name, hours, divs, wins in TIERS:
        out.append({
            "tier": tid, "name": name, "hours": hours, "divisions": divs,
            "wins": wins, "xp": hours * TYPICAL_XP_PER_HOUR, "color": TIER_COLORS[tid],
        })
    return out


# ------------------------------------------------------------- win detection
# Unity writes Player.log next to other app logs. PG3D's exact wording isn't
# documented, so the patterns are user-editable from the Rank tab.
DEFAULT_WIN_PATTERNS = [
    r"\b(victory|you win|you won|match won)\b",
    r"\b(match|battle|game)\s*(result|end(?:ed)?)\b.*\b(win|won|victory)\b",
]
DEFAULT_LOSS_GUARD = r"\b(defeat|lose|lost|draw)\b"
HINT_RE = re.compile(r"win|won|victory|defeat|match|result|round|battle ?end|game ?over", re.I)


def player_log_candidates() -> list:
    logs = Path.home() / "Library/Logs"
    out = [
        logs / "Pixel Gun Team/Pixel Gun 3D/Player.log",
        logs / "Cubic Games/Pixel Gun 3D/Player.log",
        logs / "Pixel Gun 3D/Player.log",
    ]
    try:
        out += sorted(logs.glob("*/Pixel Gun*/Player.log"))
    except OSError:
        pass
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def find_player_log():
    found = [p for p in player_log_candidates() if p.is_file()]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


class WinWatcher:
    """Tails Player.log while the game runs and reports wins via `on_win`."""

    def __init__(self, get_patterns, on_win, game_running, log=print):
        self.get_patterns = get_patterns
        self.on_win = on_win
        self.game_running = game_running
        self.log = log
        self.path = None
        self.offset = 0
        self.last_win = 0.0
        self.recent = []  # interesting lines, for the pattern helper in the UI
        self.lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def recent_lines(self):
        with self.lock:
            return list(self.recent[-60:])

    def _compile(self):
        cfg = self.get_patterns()
        pats = []
        for raw in cfg.get("patterns") or []:
            try:
                pats.append(re.compile(raw, re.I))
            except re.error:
                continue
        guard = None
        try:
            guard = re.compile(cfg.get("loss_guard") or DEFAULT_LOSS_GUARD, re.I) if cfg.get("loss_guard", DEFAULT_LOSS_GUARD) else None
        except re.error:
            guard = None
        return pats, guard, max(20, int(cfg.get("cooldown", 90) or 90))

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                self.log(f"Win watcher error: {e}")
            time.sleep(2)

    def _tick(self):
        path = find_player_log()
        if path is None:
            return
        size = path.stat().st_size
        if path != self.path:
            # Attach at the end: only lines written from now on count.
            self.path, self.offset = path, size
            return
        if size < self.offset:  # the game restarted and truncated the log
            self.offset = 0
        if size == self.offset:
            return
        with path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read(min(size - self.offset, 4 * 1024 * 1024))
        self.offset += len(chunk)
        if not self.game_running():
            return
        pats, guard, cooldown = self._compile()
        for line in chunk.decode("utf-8", "ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            if HINT_RE.search(line):
                with self.lock:
                    self.recent.append({"at": int(time.time()), "line": line[:300]})
                    self.recent = self.recent[-200:]
            if guard is not None and guard.search(line):
                continue
            if any(p.search(line) for p in pats):
                now = time.time()
                if now - self.last_win < cooldown:
                    continue
                self.last_win = now
                self.on_win(line[:300])
