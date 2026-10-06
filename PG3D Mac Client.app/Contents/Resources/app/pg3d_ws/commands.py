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


# ── progress ──

def get_progress(player_id: int, slots: list | None = None):
    return "get_progress", {"player_id": int(player_id), "slots": list(slots or [1, 3, 4])}


# ── currency ──

def add_currency(currency: str, amount: int):
    cmd = _command(10, {"c": str(currency), "v": int(amount), "ca": 16})
    return "update_progress", _snapshot([cmd])


def spend_currency(currency: str, amount: int):
    cmd = _command(11, {"c": str(currency), "v": int(amount), "ca": 16})
    return "update_progress", _snapshot([cmd])


# ── weapons ──

def add_weapon(index: int, level: int = 65, rarity: int = 5):
    cmd = _command(132, {"i": int(index), "u": {"l": int(level), "r": int(rarity)}, "oc": 9999})
    return "update_progress", _snapshot([cmd])


def add_all_weapons():
    return "add_all_weapons", {}


def add_weapon_parts(weapon_id: int, amount: int = 100):
    return "add_weapon_parts", {"weapon_id": int(weapon_id), "amount": int(amount)}


def add_all_weapon_parts():
    return "add_all_weapon_parts", {}


def remove_weapon(index: int):
    return "remove_weapon", {"index": int(index)}


def add_weapon_skin(weapon_id: int, skin_id: int):
    return "add_weapon_skin", {"weapon_id": int(weapon_id), "skin_id": int(skin_id)}


def add_all_weapon_skins():
    return "add_all_weapon_skins", {}


# ── character / equipment ──

def add_experience(amount: int):
    return "add_experience", {"amount": int(amount)}


def set_level(level: int):
    return "set_level", {"level": int(level)}


def add_armor(index: int, level: int = 65, rarity: int = 5):
    return "add_armor", {"index": int(index), "level": int(level), "rarity": int(rarity)}


def add_all_armor():
    return "add_all_armor", {}


def add_armor_skin(armor_id: int, skin_id: int):
    return "add_armor_skin", {"armor_id": int(armor_id), "skin_id": int(skin_id)}


def add_all_armor_skins():
    return "add_all_armor_skins", {}


def add_wear(index: int):
    return "add_wear", {"index": int(index)}


def add_all_wear():
    return "add_all_wear", {}


def add_wear_skin(wear_id: int, skin_id: int):
    return "add_wear_skin", {"wear_id": int(wear_id), "skin_id": int(skin_id)}


def add_all_hats():
    return "add_all_hats", {}


def add_all_masks():
    return "add_all_masks", {}


def add_all_capes():
    return "add_all_capes", {}


def add_all_boots():
    return "add_all_boots", {}


def unlock_character(character_id: int):
    return "unlock_character", {"character_id": int(character_id)}


# ── gadgets / pets / modules ──

def add_gadget(index: int, level: int = 1):
    return "add_gadget", {"index": int(index), "level": int(level)}


def add_all_gadgets():
    return "add_all_gadgets", {}


def add_pet(index: int, level: int = 1):
    return "add_pet", {"index": int(index), "level": int(level)}


def add_all_pets():
    return "add_all_pets", {}


def upgrade_pet(pet_id: int, level: int):
    return "upgrade_pet", {"pet_id": int(pet_id), "level": int(level)}


def add_module(module_id: int):
    return "add_module", {"module_id": int(module_id)}


def add_all_modules():
    return "add_all_modules", {}


def upgrade_module(module_id: int, level: int):
    return "upgrade_module", {"module_id": int(module_id), "level": int(level)}


# ── cosmetics ──

def add_graffiti(index: int):
    return "add_graffiti", {"index": int(index)}


def add_all_graffiti():
    return "add_all_graffiti", {}


def add_lobby_item(index: int):
    return "add_lobby_item", {"index": int(index)}


def add_all_lobby_items():
    return "add_all_lobby_items", {}


def add_profile_items(items: list):
    return "add_profile_items", {"items": list(items)}


# ── game / progression ──

def unlock_pixel_pass():
    return "unlock_pixel_pass", {}


def custom_pixel_pass(data: dict):
    return "custom_pixel_pass", dict(data)


def reset_pixel_pass():
    return "reset_pixel_pass", {}


def skip_tutorial():
    return "skip_tutorial", {}


def complete_campaign():
    return "complete_campaign", {}


def consumable(consumable_id: int, amount: int = 1):
    return "consumable", {"consumable_id": int(consumable_id), "amount": int(amount)}


def add_royale(data: dict | None = None):
    return "add_royale", dict(data or {})


def add_all_royale():
    return "add_all_royale", {}


def add_offers(data: dict | None = None):
    return "add_offers", dict(data or {})


# ── account ──

def set_paying_user():
    return "set_paying_user", {}


def update_monthly_stats(data: dict | None = None):
    return "update_monthly_stats", dict(data or {})


def get_binded_ids():
    return "get_binded_ids", {}


def max_account():
    return "max_account", {}


def reset_progress():
    return "reset_progress", {}


def ban_account(player_id: int):
    return "ban_account", {"player_id": int(player_id)}


def set_nickname(nickname: str):
    return "set_nickname", {"nickname": str(nickname)}


def fix_loadout():
    return "fix_loadout", {}


def add_global_stats(data: dict | None = None):
    return "add_global_stats", dict(data or {})


def veteran_badge():
    return "veteran_badge", {}


COMMAND_REGISTRY = {
    "unlock_pixel_pass":     {"fn": unlock_pixel_pass,     "args": []},
    "add_currency":          {"fn": add_currency,          "args": [("currency", "str"), ("amount", "int")]},
    "spend_currency":        {"fn": spend_currency,        "args": [("currency", "str"), ("amount", "int")]},
    "add_weapon":            {"fn": add_weapon,            "args": [("index", "int"), ("level", "int"), ("rarity", "int")]},
    "add_all_weapons":       {"fn": add_all_weapons,       "args": []},
    "add_weapon_parts":      {"fn": add_weapon_parts,      "args": [("weapon_id", "int"), ("amount", "int")]},
    "add_all_weapon_parts":  {"fn": add_all_weapon_parts,  "args": []},
    "remove_weapon":         {"fn": remove_weapon,         "args": [("index", "int")]},
    "add_experience":        {"fn": add_experience,        "args": [("amount", "int")]},
    "set_level":             {"fn": set_level,             "args": [("level", "int")]},
    "skip_tutorial":         {"fn": skip_tutorial,         "args": []},
    "add_armor":             {"fn": add_armor,             "args": [("index", "int"), ("level", "int"), ("rarity", "int")]},
    "add_all_armor":         {"fn": add_all_armor,         "args": []},
    "add_wear":              {"fn": add_wear,              "args": [("index", "int")]},
    "add_all_wear":          {"fn": add_all_wear,          "args": []},
    "complete_campaign":     {"fn": complete_campaign,      "args": []},
    "set_paying_user":       {"fn": set_paying_user,       "args": []},
    "update_monthly_stats":  {"fn": update_monthly_stats,  "args": [("data", "json")]},
    "unlock_character":      {"fn": unlock_character,      "args": [("character_id", "int")]},
    "get_binded_ids":        {"fn": get_binded_ids,        "args": []},
    "max_account":           {"fn": max_account,           "args": []},
    "reset_progress":        {"fn": reset_progress,        "args": []},
    "ban_account":           {"fn": ban_account,           "args": [("player_id", "int")]},
    "set_nickname":          {"fn": set_nickname,          "args": [("nickname", "str")]},
    "fix_loadout":           {"fn": fix_loadout,           "args": []},
    "add_global_stats":      {"fn": add_global_stats,      "args": [("data", "json")]},
    "consumable":            {"fn": consumable,            "args": [("consumable_id", "int"), ("amount", "int")]},
    "veteran_badge":         {"fn": veteran_badge,         "args": []},
    "add_royale":            {"fn": add_royale,            "args": [("data", "json")]},
    "add_all_royale":        {"fn": add_all_royale,        "args": []},
    "add_weapon_skin":       {"fn": add_weapon_skin,       "args": [("weapon_id", "int"), ("skin_id", "int")]},
    "add_all_weapon_skins":  {"fn": add_all_weapon_skins,  "args": []},
    "add_graffiti":          {"fn": add_graffiti,          "args": [("index", "int")]},
    "add_all_graffiti":      {"fn": add_all_graffiti,      "args": []},
    "add_lobby_item":        {"fn": add_lobby_item,        "args": [("index", "int")]},
    "add_all_lobby_items":   {"fn": add_all_lobby_items,   "args": []},
    "add_gadget":            {"fn": add_gadget,            "args": [("index", "int"), ("level", "int")]},
    "add_all_gadgets":       {"fn": add_all_gadgets,       "args": []},
    "add_pet":               {"fn": add_pet,               "args": [("index", "int"), ("level", "int")]},
    "add_all_pets":          {"fn": add_all_pets,          "args": []},
    "upgrade_pet":           {"fn": upgrade_pet,           "args": [("pet_id", "int"), ("level", "int")]},
    "add_profile_items":     {"fn": add_profile_items,     "args": [("items", "json")]},
    "add_module":            {"fn": add_module,            "args": [("module_id", "int")]},
    "add_all_modules":       {"fn": add_all_modules,       "args": []},
    "upgrade_module":        {"fn": upgrade_module,        "args": [("module_id", "int"), ("level", "int")]},
    "add_all_hats":          {"fn": add_all_hats,          "args": []},
    "add_all_masks":         {"fn": add_all_masks,         "args": []},
    "add_all_capes":         {"fn": add_all_capes,         "args": []},
    "add_all_boots":         {"fn": add_all_boots,         "args": []},
    "add_wear_skin":         {"fn": add_wear_skin,         "args": [("wear_id", "int"), ("skin_id", "int")]},
    "add_armor_skin":        {"fn": add_armor_skin,        "args": [("armor_id", "int"), ("skin_id", "int")]},
    "add_all_armor_skins":   {"fn": add_all_armor_skins,   "args": []},
    "add_offers":            {"fn": add_offers,            "args": [("data", "json")]},
    "custom_pixel_pass":     {"fn": custom_pixel_pass,     "args": [("data", "json")]},
    "reset_pixel_pass":      {"fn": reset_pixel_pass,      "args": []},
    "get_progress":          {"fn": get_progress,          "args": [("player_id", "int")]},
    "add_all_wear":          {"fn": add_all_wear,          "args": []},
}
