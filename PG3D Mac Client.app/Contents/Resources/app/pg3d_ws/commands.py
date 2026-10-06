"""Command builders for PG3D WebSocket protocol."""
from __future__ import annotations

import binascii
import os


def _hex(length: int = 8) -> str:
    return binascii.b2a_hex(os.urandom(length // 2)).decode("utf-8")


def _command(cmd_id: int, params: dict) -> dict:
    return {"id": int(cmd_id), "ci": _hex(8), "p": params, "h": {}, "u": [140, 6]}


def _snapshot(commands: list) -> dict:
    return {"id": 63, "i": _hex(8), "p": {"c": commands}}


def get_progress(player_id: int, slots: list | None = None):
    return "get_progress", {"player_id": int(player_id), "slots": list(slots or [1, 3, 4])}


def add_currency(currency: str, amount: int):
    cmd = _command(10, {"c": str(currency), "v": int(amount), "ca": 16})
    return "update_progress", _snapshot([cmd])


def add_weapon(index: int, level: int = 65, rarity: int = 5):
    cmd = _command(132, {"i": int(index), "u": {"l": int(level), "r": int(rarity)}, "oc": 9999})
    return "update_progress", _snapshot([cmd])
