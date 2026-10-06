"""In-game GUI theme recolouring and custom skybox support.

Both features edit Pixel Gun 3D's Unity asset files through the same
backup/rebuild pipeline server.py already uses for cursors and GUI sprites.
This module holds the image maths and the asset scanners; server.py decides
when files are rebuilt.
"""
from __future__ import annotations

import colorsys
import math
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

try:  # numpy makes panorama projection ~100x faster; it is optional.
    import numpy as np
except Exception:  # pragma: no cover - depends on the runtime
    np = None


# --------------------------------------------------------------------- themes
# Each preset is a gradient map: the luminance of every GUI pixel is looked up
# along these stops. Dark pixels take the first colour, bright ones the last,
# so outlines, shading and text contrast survive the recolour.
THEMES = {
    "crimson": {
        "name": "Crimson",
        "desc": "Blood red and black with ember highlights.",
        "strength": 1.0,
        "remap": dict(primary=352, accent=18, spread=0.18, sat=1.05, val=0.92, tint=0.10, tint_hue=352),
        "stops": [(0.0, "#0A0203"), (0.30, "#3D060D"), (0.58, "#A3101E"), (0.82, "#FF4A3A"), (1.0, "#FFF0EC")],
    },
    "dark_souls": {
        "name": "Dark Souls",
        "desc": "Ashen blue-green over near-black, like a bonfire gone cold.",
        "strength": 1.0,
        "remap": dict(primary=168, accent=42, spread=0.10, sat=0.55, val=0.66, tint=0.20, tint_hue=170),
        "stops": [(0.0, "#040706"), (0.30, "#0E1C1C"), (0.58, "#2B4A46"), (0.82, "#86A597"), (1.0, "#E6EDE3")],
    },
    "royal": {
        "name": "Royal",
        "desc": "Deep violet and velvet with gold trim.",
        "strength": 1.0,
        "remap": dict(primary=268, accent=45, spread=0.16, sat=0.95, val=0.95, tint=0.10, tint_hue=268),
        "stops": [(0.0, "#0A0517"), (0.30, "#24105A"), (0.56, "#5528A8"), (0.80, "#D4AE55"), (1.0, "#FFF6DA")],
    },
    "icy": {
        "name": "Icy",
        "desc": "Glacier blue fading to frost white.",
        "strength": 1.0,
        "remap": dict(primary=200, accent=188, spread=0.14, sat=0.72, val=1.04, tint=0.10, tint_hue=205),
        "stops": [(0.0, "#020C16"), (0.30, "#0B3352"), (0.58, "#2F93CF"), (0.82, "#A6E4FF"), (1.0, "#FFFFFF")],
    },
    "cute": {
        "name": "Cute",
        "desc": "Soft pink with deeper rose accents.",
        "strength": 1.0,
        "remap": dict(primary=328, accent=344, spread=0.12, sat=0.82, val=1.05, tint=0.14, tint_hue=332),
        "stops": [(0.0, "#1A0611"), (0.30, "#5C1A3A"), (0.58, "#C2457E"), (0.82, "#FF9CC8"), (1.0, "#FFF0F6")],
    },
}


def _hex(value):
    h = value.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def theme_luts(preset: str):
    """Three 256-entry lookup tables (R, G, B) for a preset's gradient map."""
    stops = [(p, _hex(c)) for p, c in THEMES[preset]["stops"]]
    luts = ([], [], [])
    for i in range(256):
        t = i / 255.0
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if t <= p1:
                f = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                for ch in range(3):
                    luts[ch].append(int(round(c0[ch] + (c1[ch] - c0[ch]) * f)))
                break
        else:
            for ch in range(3):
                luts[ch].append(stops[-1][1][ch])
    return luts


_LUT_CACHE = {}


def _remap_luts(preset):
    """Lookup tables for one preset's palette remap (hue, saturation, brightness, tint)."""
    p = THEMES[preset]["remap"]
    hue = []
    for i in range(256):
        h = i / 256 * 360
        if 20 <= h <= 75:            # golds, oranges, yellows: PG3D's highlight colors
            nh = p["accent"] + (h - 47) * 0.3
        else:                        # everything else keeps part of its spread around blue
            d = ((h - 210 + 180) % 360) - 180
            nh = p["primary"] + d * p["spread"]
        hue.append(int(round((nh % 360) / 360 * 256)) % 256)
    sat = [min(255, int(v * p["sat"])) for v in range(256)]
    val = [min(255, int(255 * ((v / 255) ** (1 / p["val"])))) for v in range(256)]
    mask = [0 if v < 28 else 255 if v > 70 else int((v - 28) / 42 * 255) for v in range(256)]
    tr, tg, tb = [int(c * 255) for c in colorsys.hsv_to_rgb(p["tint_hue"] / 360, 0.5, 1)]
    t = p["tint"]
    tint = (int(255 - (255 - tr) * t), int(255 - (255 - tg) * t), int(255 - (255 - tb) * t))
    return hue, sat, val, mask, tint


def recolor_image(img: Image.Image, preset: str, strength: float = 1.0) -> Image.Image:
    """Retexture an RGBA image into a theme's palette, keeping its alpha untouched.

    Each pixel keeps its own brightness and detail; colored pixels move into the
    theme's hues (warm highlights to the accent hue, the rest around the main hue,
    keeping part of their original spread), and near-neutral pixels such as white
    text, greys and outlines stay neutral with a light tint. Purely per-pixel, so
    recoloring an atlas and then cropping a sprite gives the same pixels as
    cropping first and recoloring after.
    """
    if preset not in THEMES:
        return img
    strength = max(0.0, min(1.0, float(strength)))
    rgba = img.convert("RGBA")
    if strength <= 0:
        return rgba
    luts = _LUT_CACHE.get(preset)
    if luts is None:
        luts = _LUT_CACHE[preset] = _remap_luts(preset)
    hue, sat, val, mask_lut, tint = luts
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    H, S, V = rgb.convert("HSV").split()
    themed = Image.merge("HSV", (H.point(hue), S.point(sat), V.point(val))).convert("RGB")
    neutral = ImageChops.multiply(rgb, Image.new("RGB", rgb.size, tint)).point(val * 3)
    out = Image.composite(themed, neutral, S.point(mask_lut))
    if strength < 1:
        out = Image.blend(rgb, out, strength)
    out = out.convert("RGBA")
    out.putalpha(alpha)
    return out


def theme_swatch(preset: str, width=240, height=24) -> Image.Image:
    luts = theme_luts(preset)
    strip = Image.new("RGB", (256, 1))
    strip.putdata([(luts[0][i], luts[1][i], luts[2][i]) for i in range(256)])
    return strip.resize((width, height), Image.Resampling.BILINEAR)


# -------------------------------------------------------------- unity helpers
GUI_SKIP_WORDS = ("sky", "lightmap", "normal", "_nrm", "terrain", "shadowmask", "reflection", "noise", "lut")
SKY_FACE_TOKENS = {
    "front": {"front", "ft", "fr", "forward", "pz", "posz"},
    "back": {"back", "bk", "bc", "behind", "nz", "negz"},
    "left": {"left", "lf", "le", "nx", "negx"},
    "right": {"right", "rt", "ri", "px", "posx"},
    "up": {"up", "top", "py", "posy"},
    "down": {"down", "dn", "bottom", "bt", "ny", "negy"},
}
SKYBOX_MATERIAL_SLOTS = {
    "_FrontTex": "front", "_BackTex": "back", "_LeftTex": "left",
    "_RightTex": "right", "_UpTex": "up", "_DownTex": "down",
    "_Tex": "cube", "_MainTex": "panorama",
}
ALPHA_ONLY_FORMATS = {1, 9, 63}  # Alpha8, R16, R8 (font atlases / masks)


def _own_objects(env):
    """Objects of the scanned file only. Resolving a material's shader makes UnityPy
    load other game files into the same environment; their objects must not be
    recorded under this file (applying then found nothing and skipped them)."""
    own = getattr(env, "_pg3d_own_files", None)
    objs = env.objects
    return objs if own is None else [o for o in objs if id(o.assets_file) in own]


def _name(obj):
    try:
        return obj.peek_name() or ""
    except Exception:
        try:
            return getattr(obj.parse_as_object(), "m_Name", "") or ""
        except Exception:
            return ""


def _pptr_ids(pptr):
    fid = getattr(pptr, "m_FileID", getattr(pptr, "file_id", 0))
    pid = getattr(pptr, "m_PathID", getattr(pptr, "path_id", 0))
    return int(fid or 0), int(pid or 0)


def _tex_env_items(material):
    try:
        envs = material.m_SavedProperties.m_TexEnvs
    except Exception:
        return []
    out = []
    for entry in envs or []:
        try:
            if isinstance(entry, (tuple, list)):
                key, env = entry[0], entry[1]
            else:
                key, env = entry.first, entry.second
            key = getattr(key, "name", key)
            out.append((str(key), env))
        except Exception:
            continue
    return out


def _gui_textures_in(env, hints, log, label):
    """Texture2Ds in one loaded file that back a UI Sprite or carry a GUI name."""
    sprite_textures = set()
    textures = {}
    for obj in _own_objects(env):
        tname = obj.type.name
        try:
            if tname == "Sprite":
                spr = obj.parse_as_object()
                fid, pid = _pptr_ids(spr.m_RD.texture)
                if fid == 0 and pid:
                    sprite_textures.add(pid)
            elif tname == "SpriteAtlas":
                for _key, rd in obj.parse_as_object().m_RenderDataMap:
                    fid, pid = _pptr_ids(rd.texture)
                    if fid == 0 and pid:
                        sprite_textures.add(pid)
            elif tname == "Texture2D":
                textures[int(obj.path_id)] = obj
        except Exception as e:
            log(f"THEME OBJ FAIL {label} {getattr(obj, 'path_id', '?')}: {e}")
    found = []
    for pid, obj in textures.items():
        name = _name(obj)
        low = name.lower()
        if any(w in low for w in GUI_SKIP_WORDS):
            continue
        if pid not in sprite_textures and not any(h in low for h in hints):
            continue
        try:
            tex = obj.parse_as_object()
            w, h = int(tex.m_Width), int(tex.m_Height)
            if int(tex.m_TextureFormat) in ALPHA_ONLY_FORMATS:
                continue
            if not (4 <= w <= 8192 and 4 <= h <= 8192):
                continue
        except Exception:
            continue
        found.append({"path_id": pid, "name": name, "width": w, "height": h})
    return found


def _face_from_name(name: str):
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    tokens = set(re.split(r"[^a-z0-9]+", spaced.lower()))
    for face, words in SKY_FACE_TOKENS.items():
        if tokens & words:
            return face
    return None


def _sky_textures_in(env, p: Path, app: Path, log):
    """Skybox textures in one loaded file: via Skybox materials, then by name."""
    out = []
    faces = {}  # path id -> face from a skybox material
    tex_objs = {}
    for obj in _own_objects(env):
        tname = obj.type.name
        if tname in ("Texture2D", "Cubemap"):
            tex_objs[int(obj.path_id)] = obj
        elif tname == "Material":
            try:
                mat = obj.parse_as_object()
                shader_name = ""
                try:
                    sh = mat.m_Shader.deref()
                    shader_name = (_name(sh) if sh is not None else "") or ""
                except Exception:
                    pass
                # Only real sky materials: Unity's Skybox/* shaders or a shader named
                # like a sky. A weapon material called "SkyHammer" is not one.
                if "skybox" not in shader_name.lower() and not is_sky_name(shader_name.replace("/", " "), _SHADER_WORDS):
                    continue
                for key, env_ in _tex_env_items(mat):
                    slot = SKYBOX_MATERIAL_SLOTS.get(key)
                    if not slot:
                        continue
                    fid, pid = _pptr_ids(env_.m_Texture)
                    if fid == 0 and pid:
                        faces[pid] = slot
            except Exception as e:
                log(f"SKY MAT FAIL {p} {getattr(obj, 'path_id', '?')}: {e}")
    for pid, obj in tex_objs.items():
        name = _name(obj)
        face = faces.get(pid)
        if face is None and not is_sky_name(name):
            continue
        try:
            tex = obj.parse_as_object()
            w, h = int(tex.m_Width), int(tex.m_Height)
        except Exception:
            continue
        if w < 16 or h < 16:
            continue
        thumb = None
        try:
            thumb = tex.image.convert("RGB")
            thumb.thumbnail((192, 192))
        except Exception:
            pass
        typ = obj.type.name
        if typ == "Cubemap":
            face = "cube"
        elif face in (None, "cube"):
            face = _face_from_name(name) or ("panorama" if w >= 1.7 * h else "flat")
        try:
            rel = str(p.relative_to(app))
        except ValueError:
            rel = p.name
        out.append({
            "source_file": str(p), "relative_file": rel, "path_id": int(pid),
            "name": name, "type": typ, "face": face, "width": w, "height": h,
            "from_material": pid in faces, "_thumb": thumb,
        })
    return out


def _words(name: str):
    """Lower-case words of a texture name: "UILoadingBG_2" -> {"ui", "loading", "bg", "2"}."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name or "")
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return {w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w}


def is_loading_name(name: str) -> bool:
    # Whole words only: weapon "Reloading" animations matched the old substring test
    # and got the loading picture pasted over them.
    w = _words(name)
    if w & {"reload", "reloading", "reloader"}:
        return False
    return bool(w & {"loading", "splash", "loadscreen", "loadingscreen", "preloader"}
                or {"load", "screen"} <= w or {"loader", "bg"} <= w)


_LOADING_HINTS = {"map", "maps", "preview", "previews", "background", "bg", "lobby", "menu", "art", "arts", "banner",
                  "arena", "level", "mode", "event", "season", "poster", "wallpaper", "screen", "roulette"}
_NOT_LOADING = {"reload", "reloading", "weapon", "gun", "skin", "icon", "button", "atlas", "font", "normal", "mask",
                "lightmap", "shadowmask", "noise", "ramp", "lut", "sprite", "sheet", "anim", "fx", "particle"}


def is_loading_candidate(name: str, w: int, h: int) -> bool:
    """Screen-sized art that may be shown while a map loads (map previews and the
    like). Offered in the picker, not ticked by default."""
    words = _words(name)
    return (w >= 1024 and 1.45 <= w / max(1, h) <= 2.5 and bool(words & _LOADING_HINTS)
            and not words & _NOT_LOADING)


_LOADING_STRONG = {"preview", "season", "bg", "background", "previews"}


def loading_suggested(name: str, w: int, h: int) -> bool:
    """Ticked by default: named as loading, or season/preview/background art at
    screen size (the player's report showed these are what PG3D shows)."""
    return is_loading_name(name) or (is_loading_candidate(name, w, h) and w >= 1024 and bool(_words(name) & _LOADING_STRONG))


_SKY_WORD = re.compile(r"^(sky|skies|skybox|skydome|skysphere|skymap|skytex)\d*$")


_SKY_VOCAB = {"night", "day", "dawn", "dusk", "sunset", "sunrise", "evening", "morning", "cloud", "clouds",
              "cloudy", "space", "stars", "star", "galaxy", "blue", "dark", "red", "orange", "purple", "main",
              "front", "back", "left", "right", "up", "down", "top", "bottom", "ft", "bk", "lf", "rt", "dn",
              "px", "nx", "py", "ny", "pz", "nz", "pos", "neg", "posx", "negx", "posy", "negy", "posz", "negz",
              "x", "y", "z", "tex", "texture", "hdr", "hdri", "cube", "cubemap", "panorama", "pano", "box",
              "dome", "map", "bg", "background", "lowres", "hd", "lod", "new", "old", "copy"}


_NOT_SKY = {"weapon", "gun", "rifle", "pistol", "shotgun", "sniper", "sword", "blade", "hammer", "axe", "bow", "knife",
            "launcher", "rocket", "jet", "skin", "icon", "btn", "button", "fx", "vfx", "particle", "particles", "effect",
            "trail", "bullet", "ammo", "armor", "armour", "pet", "gadget", "fall", "lander", "walker", "rider", "diver",
            "hook", "mask", "normal", "nrm", "lightmap", "shadow", "decal", "logo", "badge", "avatar", "hat", "cape"}


def is_sky_name(name: str, extra=()) -> bool:
    """A sky-word in the name and nothing that marks a weapon, effect or item:
    "skybox_26_foggy_night", "26.1_lobby_sky", "infernal_forge_Sky" yes;
    "SkyHammer_diffuse", "Weapon_SkyFall", "Skyline" no."""
    words = _words(name)
    return any(_SKY_WORD.match(x) for x in words) and not words & _NOT_SKY


_SHADER_WORDS = {"custom", "unlit", "mobile", "legacy", "shader", "shaders", "pg", "pg3d", "standard", "simple"}




def _loading_textures_in(env, p: Path, app: Path, log):
    """Big Texture2Ds named like loading screens / splash art."""
    out = []
    for obj in _own_objects(env):
        if obj.type.name != "Texture2D":
            continue
        name = _name(obj)
        sure = is_loading_name(name)
        if not sure and not _words(name) & _LOADING_HINTS:
            continue
        try:
            tex = obj.parse_as_object()
            w, h = int(tex.m_Width), int(tex.m_Height)
            # A loading screen fills the screen: landscape, roughly 4:3 to 21:9.
            if int(tex.m_TextureFormat) in ALPHA_ONLY_FORMATS or w < 512 or h < 256 or not 1.2 <= w / h <= 2.5:
                continue
            if not sure and not is_loading_candidate(name, w, h):
                continue
            thumb = tex.image.convert("RGB")
            thumb.thumbnail((320, 320))
        except Exception as e:
            log(f"LOADING TEX FAIL {p} {getattr(obj, 'path_id', '?')}: {e}")
            continue
        try:
            rel = str(p.relative_to(app))
        except ValueError:
            rel = p.name
        out.append({"source_file": str(p), "relative_file": rel, "path_id": int(obj.path_id),
                    "name": name, "width": w, "height": h, "suggested": sure or loading_suggested(name, w, h),
                    "_thumb": thumb})
    return out


FONT_SKIP_RE = re.compile(r"icon|symbol|emoji|awesome|material|glyph|dingbat|cjk|chinese|japan|korea|arab|thai|hebrew|"
                          r"devanagari|noto.?sans.?(sc|tc|jp|kr)|\b(jp|kr|zh|sc|tc)\b", re.I)


def _fonts_in(env, p: Path, app: Path, log):
    """Unity Font objects that carry their own TTF/OTF data (dynamic fonts)."""
    out = []
    for obj in _own_objects(env):
        if obj.type.name != "Font":
            continue
        try:
            font = obj.parse_as_object()
            size = len(font.m_FontData or [])
        except Exception as e:
            log(f"FONT FAIL {p} {getattr(obj, 'path_id', '?')}: {e}")
            continue
        name = getattr(font, "m_Name", "") or ""
        if size < 1024 or FONT_SKIP_RE.search(name):
            continue
        try:
            rel = str(p.relative_to(app))
        except ValueError:
            rel = p.name
        out.append({"source_file": str(p), "relative_file": rel, "path_id": int(obj.path_id), "name": name, "bytes": size})
    return out


def replace_font_data(font, data: bytes):
    """Swap the embedded font file; glyphs are rendered from it at runtime."""
    font.m_FontData = list(data)


def scan_assets(files, app: Path, hints, progress=None, log=print, sample_cb=None, max_samples=10, loading=None, fonts=None):
    """One pass over the game files for both features.

    Returns (gui_targets, sky_textures): gui_targets maps source file ->
    [{"path_id", "name", "width", "height"}]; sky_textures is a list.
    """
    import UnityPy

    gui, sky = {}, []
    total = max(1, len(files))
    for idx, p in enumerate(files, 1):
        if progress:
            progress(idx / total, f"Scanning game files ({idx}/{total})…")
        try:
            env = UnityPy.load(str(p))
            env._pg3d_own_files = {id(o.assets_file) for o in env.objects}
        except Exception as e:
            log(f"CUSTOMIZE LOAD FAIL {p}: {type(e).__name__}: {e}")
            continue
        found = _gui_textures_in(env, hints, log, p)
        if found:
            gui[str(p)] = found
            if sample_cb and max_samples > 0:
                # Keep a thumbnail of this file's biggest atlas for theme previews.
                big = max(found, key=lambda t: t["width"] * t["height"])
                try:
                    reader = next(o for o in env.objects if int(o.path_id) == big["path_id"])
                    img = reader.parse_as_object().image.convert("RGBA")
                    img.thumbnail((420, 420))
                    sample_cb(img, big)
                    max_samples -= 1
                except Exception as e:
                    log(f"Theme sample failed for {p}: {e}")
        sky += _sky_textures_in(env, Path(p), app, log)
        if loading is not None:
            loading += _loading_textures_in(env, Path(p), app, log)
        if fonts is not None:
            fonts += _fonts_in(env, Path(p), app, log)
        del env
    sky.sort(key=lambda t: (not t["from_material"], t["relative_file"], t["name"].lower()))
    for i, t in enumerate(sky, 1):
        t["id"] = i
    if loading is not None:
        loading.sort(key=lambda t: -(t["width"] * t["height"]))
        for i, t in enumerate(loading, 1):
            t["id"] = i
    return gui, sky


# ------------------------------------------------------------------ skyboxes
# View direction for pixel (a, b) of each face, a right / b down in [-1, 1],
# Unity's left-handed space: +X right, +Y up, +Z forward.
_FACE_DIRS = {
    "front": lambda a, b: (a, -b, 1.0),
    "back": lambda a, b: (-a, -b, -1.0),
    "right": lambda a, b: (1.0, -b, -a),
    "left": lambda a, b: (-1.0, -b, a),
    "up": lambda a, b: (a, 1.0, b),
    "down": lambda a, b: (a, -1.0, -b),
}
SKY_MIN_FACE = 0  # keep each texture's own size (1.49.0 enlarged them; safer to leave sizes alone)
CUBEMAP_ORDER = ("right", "left", "up", "down", "front", "back")  # +X -X +Y -Y +Z -Z


def is_panorama(img: Image.Image):
    # Only true 360x180 (2:1) images; a 16:9 photo (1.78) is not one and looked
    # stretched with a seam when it was wrapped around the whole sky.
    return 1.95 <= img.width / max(1, img.height) <= 2.05


def as_panorama(img: Image.Image, height=None) -> Image.Image:
    """A 2:1 panorama for the sky. Panoramas pass through; a normal photo becomes
    a band around the horizon (tiled with mirrored copies, so it wraps without a
    seam) with its top and bottom colours drawn out towards the zenith and ground.
    Without this every cube face showed the same cropped photo with hard edges."""
    if is_panorama(img):
        return img.convert("RGB")
    key = (id(img), img.size, height)
    if _PANO_CACHE.get("key") == key:
        return _PANO_CACHE["pano"]
    pano = _photo_to_panorama(img.convert("RGB"), height)
    _PANO_CACHE.update(key=key, pano=pano)
    return pano


_PANO_CACHE = {}


def _photo_to_panorama(src: Image.Image, height=None) -> Image.Image:
    H = int(height) if height else max(1024, min(2048, src.height * 2))
    W = 2 * H
    # The photo spans 60 degrees of height (15 below to 45 above the horizon) and a
    # quarter of the way around; four tiles alternate normal / mirrored.
    top, bottom = round(H * (0.5 - 45 / 180)), round(H * (0.5 + 15 / 180))
    tile = cover(src, (W // 4, bottom - top)).convert("RGB")
    band = Image.new("RGB", (W, bottom - top))
    mirrored = tile.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    for i in range(4):
        band.paste(tile if i % 2 == 0 else mirrored, (i * (W // 4), 0))
    pano = Image.new("RGB", (W, H))
    blur = ImageFilter.GaussianBlur(max(4, H // 48))
    # Sky above: the band's top rows stretched up, softened, fading to their average colour.
    sky = band.crop((0, 0, W, 4)).resize((W, top), Image.Resampling.BILINEAR).filter(blur)
    sky_avg = Image.new("RGB", (W, top), tuple(int(c) for c in band.crop((0, 0, W, 4)).resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))))
    fade = Image.linear_gradient("L").resize((W, top))  # 0 at zenith -> 255 at band
    pano.paste(Image.composite(sky, sky_avg, fade), (0, 0))
    # Ground below: the bottom rows drawn down and darkened a little.
    ground_h = H - bottom
    ground = band.crop((0, band.height - 4, W, band.height)).resize((W, ground_h), Image.Resampling.BILINEAR).filter(blur)
    dark = Image.new("RGB", (W, ground_h), (0, 0, 0))
    ground = Image.blend(ground, dark, 0.25)
    pano.paste(ground, (0, bottom))
    pano.paste(band, (0, top))
    # Soften the two seams between the photo and the generated parts.
    for y in (top, bottom):
        strip = pano.crop((0, max(0, y - 12), W, min(H, y + 12))).filter(ImageFilter.GaussianBlur(6))
        pano.paste(strip, (0, max(0, y - 12)))
    return pano


def cover(img: Image.Image, size):
    """Scale and centre-crop to fill `size` (like CSS object-fit: cover)."""
    w, h = size
    src = img.convert("RGBA")
    scale = max(w / src.width, h / src.height)
    nw, nh = max(1, round(src.width * scale)), max(1, round(src.height * scale))
    src = src.resize((nw, nh), Image.Resampling.LANCZOS)
    x, y = (nw - w) // 2, (nh - h) // 2
    return src.crop((x, y, x + w, y + h))


def project_face(pano: Image.Image, face: str, size: int, yaw_deg: float = 0.0) -> Image.Image:
    """Cut one cube face out of an equirectangular panorama."""
    pano = pano.convert("RGB")
    yaw = math.radians(yaw_deg)
    if np is not None:
        src = np.asarray(pano, dtype=np.float32)
        ph, pw = src.shape[:2]
        coords = (np.arange(size, dtype=np.float32) + 0.5) / size * 2 - 1
        a, b = np.meshgrid(coords, coords)
        x, y, z = _FACE_DIRS[face](a, b)
        x = np.broadcast_to(x, a.shape)
        y = np.broadcast_to(y, a.shape)
        z = np.broadcast_to(z, a.shape)
        lon = np.arctan2(x, z) + yaw
        lat = np.arctan2(y, np.sqrt(x * x + z * z))
        u = ((lon / (2 * np.pi) + 0.5) % 1.0) * pw - 0.5
        v = (0.5 - lat / np.pi) * ph - 0.5
        u0 = np.floor(u).astype(np.int64)
        v0 = np.clip(np.floor(v).astype(np.int64), 0, ph - 1)
        v1 = np.clip(v0 + 1, 0, ph - 1)
        fu = (u - u0)[..., None]
        fv = np.clip(v - np.floor(v), 0, 1)[..., None]
        u1 = (u0 + 1) % pw
        u0 = u0 % pw
        top = src[v0, u0] * (1 - fu) + src[v0, u1] * fu
        bot = src[v1, u0] * (1 - fu) + src[v1, u1] * fu
        out = top * (1 - fv) + bot * fv
        return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    # Pure-Python fallback: nearest sample on a reduced grid, then upscale.
    step = max(1, size // 256)
    small = max(1, size // step)
    pw, ph = pano.size
    px = pano.load()
    out = Image.new("RGB", (small, small))
    opx = out.load()
    fn = _FACE_DIRS[face]
    for j in range(small):
        b = (j + 0.5) / small * 2 - 1
        for i in range(small):
            a = (i + 0.5) / small * 2 - 1
            x, y, z = fn(a, b)
            lon = math.atan2(x, z) + yaw
            lat = math.atan2(y, math.sqrt(x * x + z * z))
            u = int(((lon / (2 * math.pi) + 0.5) % 1.0) * pw) % pw
            v = min(ph - 1, max(0, int((0.5 - lat / math.pi) * ph)))
            opx[i, j] = px[u, v]
    if small != size:
        out = out.resize((size, size), Image.Resampling.BICUBIC)
    return out.convert("RGBA")


def sky_image_for(target: dict, source: Image.Image, yaw=0.0, swap_lr=False, flat_mode="cover"):
    """Build the replacement image for one sky texture (not cubemaps)."""
    face = target.get("face") or "flat"
    if face == "dome":
        face = "panorama"  # a sky dome whose mesh couldn't be read: wrap the picture around
    w, h = int(target["width"]), int(target["height"])
    if swap_lr and face in ("left", "right"):
        face = "right" if face == "left" else "left"
    if face in _FACE_DIRS:
        size = max(w, h, SKY_MIN_FACE)
        return project_face(as_panorama(source), face, size, yaw)
    if face == "panorama":
        source = as_panorama(source)
    pano = is_panorama(source)
    # panoramic / unknown single texture: use the whole picture
    if pano and face == "panorama" and yaw:
        shift = int(round(yaw / 360.0 * source.width)) % source.width
        source = ImageChops.offset(source.convert("RGBA"), -shift, 0)
    if face == "panorama" or flat_mode != "cover":
        return source.convert("RGBA").resize((w, h), Image.Resampling.LANCZOS)
    return cover(source, (w, h))


def cubemap_faces(source: Image.Image, size: int, yaw=0.0, swap_lr=False):
    pano = as_panorama(source)
    size = max(int(size), SKY_MIN_FACE)
    faces = []
    for face in CUBEMAP_ORDER:
        f = face
        if swap_lr and face in ("left", "right"):
            f = "right" if face == "left" else "left"
        faces.append(project_face(pano, f, size, yaw))
    return faces


def write_cubemap(cube, faces, flip=True):
    """Replace all six faces of a Unity Cubemap object (mipmaps dropped)."""
    from UnityPy.export import Texture2DConverter

    platform = cube.object_reader.platform if cube.object_reader is not None else 0
    blob = getattr(cube, "m_PlatformBlob", None)
    chunks, fmt = [], None
    for img in faces:
        data, fmt = Texture2DConverter.image_to_texture2d(img, cube.m_TextureFormat, platform, blob, flip=flip)
        chunks.append(data)
    cube.m_Width = faces[0].width
    cube.m_Height = faces[0].height
    cube.image_data = b"".join(chunks)
    cube.m_CompleteImageSize = len(chunks[0])
    cube.m_ImageCount = len(chunks)
    cube.m_TextureFormat = fmt
    if getattr(cube, "m_MipMap", None) is not None:
        cube.m_MipMap = False
    if getattr(cube, "m_MipCount", None) is not None:
        cube.m_MipCount = 1
    if getattr(cube, "m_StreamData", None) is not None:
        cube.m_StreamData.path = ""
        cube.m_StreamData.offset = 0
        cube.m_StreamData.size = 0


def safe_sky_name(raw: str):
    s = re.sub(r"[^A-Za-z0-9 _.-]+", "", str(raw or "")).strip()[:48]
    return s or "Custom sky"


# ------------------------------------------------------------------ finder
# A broad, temporary look through the game files for the three things the client
# replaces, so the player can point at the right textures. Errs on showing too
# much: the player chooses.
_FINDER_CURSOR = {"cursor", "pointer", "mouse", "arrow", "hand", "crosshair", "reticle", "aim", "sight", "scope",
                  "click", "pointer"}
_FINDER_SKY = {"cloud", "clouds", "horizon", "space", "stars", "galaxy", "panorama", "pano", "backdrop", "hdri",
               "nebula", "aurora", "sun", "moon", "night", "skydome", "skybox"}
_FINDER_LOADING = _LOADING_HINTS | {"loading", "splash", "load", "loadscreen", "preloader", "intro", "title"}


def _finder_category(name, typ, w, h):
    words = _words(name)
    low = (name or "").lower()
    out = []
    if typ in ("Texture2D", "Sprite") and max(w, h) <= 512 and (words & _FINDER_CURSOR or "cursor" in low):
        out.append("cursor")
    if typ == "Cubemap" or (max(w, h) >= 256 and (any(x.startswith("sky") for x in words) or words & _FINDER_SKY)):
        out.append("sky")
    if (typ == "Texture2D" and w >= 512 and h >= 256 and 1.2 <= w / max(1, h) <= 2.5
            and words & _FINDER_LOADING and not words & {"reload", "reloading"}):
        out.append("loading")
    return out


_FINDER_GUI = {"ui", "hud", "button", "btn", "icon", "panel", "frame", "bar", "health", "armor", "ammo", "slot",
               "window", "popup", "tab", "badge", "arrow", "joystick", "minimap", "map", "kill", "score", "timer",
               "chat", "menu", "lobby", "shop", "gui", "hp", "shield", "reload", "grenade", "gadget", "crosshair"}


# How likely a texture is something on screen during a match that covers the view.
_VIEW_BLOCKERS = {"vignette": 60, "blood": 55, "damage": 45, "hurt": 45, "injury": 45, "overlay": 45, "flash": 40,
                  "flashbang": 50, "smoke": 35, "scope": 40, "zoom": 30, "fog": 30, "screen": 25, "splash": 25,
                  "indicator": 35, "direction": 25, "hit": 25, "lowhp": 45, "low": 15, "health": 20, "hp": 20,
                  "shield": 15, "armor": 15, "frame": 15, "border": 20, "banner": 25, "streak": 30, "killstreak": 35,
                  "kill": 20, "feed": 20, "notification": 25, "notify": 25, "popup": 20, "hint": 20, "tutorial": 20,
                  "joystick": 30, "touch": 25, "button": 10, "btn": 10, "crosshair": 15, "reticle": 15, "aim": 15,
                  "hud": 20, "bar": 10, "ammo": 15, "timer": 15, "score": 10, "minimap": 20,
                  "radar": 20, "compass": 20, "chat": 15, "emote": 20, "mvp": 20, "medal": 20, "achievement": 20,
                  "ui": 5, "gui": 5}


# Items, cosmetics and shop art are sprites too, but never part of the match HUD.
_GUI_NOT = {"avatar", "avatars", "skin", "skins", "weapon", "weapons", "gun", "guns", "rifle", "pistol", "shotgun",
            "sniper", "smg", "minigun", "launcher", "sword", "knife", "blade", "axe", "hammer", "bow", "pet", "pets",
            "gadget", "gadgets", "hat", "cape", "mask", "boots", "emblem", "flag", "sticker", "graffiti", "lootbox",
            "chest", "offer", "bundle", "pack", "shop", "store", "preview", "portrait", "character", "hero", "boss",
            "item", "items", "reward", "coin", "coins", "gem", "gems", "ticket", "season", "event", "roulette",
            "lottery", "clan", "profile", "rank", "league", "trophy", "achievement", "frame_avatar", "wear", "armor_set",
            "module", "upgrade", "craft", "recipe", "map", "maps", "lobby", "anim", "animation", "sheet"}


_GUI_STRONG = {"vignette", "blood", "overlay", "flash", "flashbang", "scope", "damage", "hurt", "smoke", "fog",
               "lowhp", "indicator", "splatter"}  # these cover the view even when named after a weapon


def gui_view_score(name, typ, w, h):
    words = _words(name)
    if words & _GUI_NOT and not words & _GUI_STRONG:
        return 0
    score = sum(v for k, v in _VIEW_BLOCKERS.items() if k in words)
    if score and max(w, h) >= 512:
        score += 20  # big: covers more of the screen
    if score and typ == "Sprite":
        score += 5
    return score


def _finder_gui(name, typ, w, h):
    words = _words(name)
    return typ == "Sprite" or (typ == "Texture2D" and max(w, h) <= 1024 and bool(words & _FINDER_GUI))


def finder_scan(files, app: Path, progress=None, log=print):
    """Every texture, sprite and cubemap in the game (names and sizes only; pictures
    are made when they're looked at). Each carries the groups it probably fits."""
    import UnityPy

    items = []
    total = max(1, len(files))
    for idx, p in enumerate(files, 1):
        if progress:
            progress(idx / total, f"Listing textures ({idx}/{total})…")
        try:
            env = UnityPy.load(str(p))
            env._pg3d_own_files = {id(o.assets_file) for o in env.objects}
        except Exception as e:
            log(f"FINDER LOAD FAIL {p}: {e}")
            continue
        try:
            sky_mat = {t["path_id"] for t in _sky_textures_in(env, Path(p), app, lambda *_: None) if t.get("from_material")}
        except Exception:
            sky_mat = set()
        try:
            rel = str(Path(p).relative_to(app))
        except ValueError:
            rel = Path(p).name
        for obj in _own_objects(env):
            typ = obj.type.name
            if typ not in ("Texture2D", "Sprite", "Cubemap"):
                continue
            name = _name(obj) or ""
            try:
                data = obj.parse_as_object()
                if typ == "Sprite":
                    r = data.m_Rect
                    w, h = int(r.width), int(r.height)
                else:
                    w, h = int(data.m_Width), int(data.m_Height)
            except Exception:
                continue
            if w < 4 or h < 4:
                continue
            cats = _finder_category(name, typ, w, h)
            if int(obj.path_id) in sky_mat and "sky" not in cats:
                cats.append("sky")
            gs = gui_view_score(name, typ, w, h) if typ != "Cubemap" else 0
            if gs:
                cats.append("gui")
            items.append({"gid": len(items), "name": name, "type": typ, "width": w, "height": h,
                          "source_file": str(p), "relative_file": rel, "path_id": int(obj.path_id), "cats": cats,
                          "gs": gs})
        del env
    return items


def finder_thumb(source_file, path_id, out: Path, env_cache):
    """A small JPEG of one texture, made on demand. env_cache keeps the last files open."""
    import UnityPy

    env = env_cache.get(source_file)
    if env is None:
        env = UnityPy.load(source_file)
        env_cache[source_file] = env
        while len(env_cache) > 2:
            env_cache.pop(next(iter(env_cache)))
    reader = next((o for o in env.objects if int(o.path_id) == int(path_id)), None)
    if reader is None:
        raise FileNotFoundError(path_id)
    data = reader.parse_as_object()
    if reader.type.name == "Cubemap":
        raise ValueError("cubemap")
    img = data.image.convert("RGBA")
    img.thumbnail((180, 180))
    bg = Image.new("RGB", img.size, (24, 24, 30))
    bg.paste(img, mask=img.split()[3])
    out.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out, "JPEG", quality=78)


# ----------------------------------------------------------------------------- HUD layout scan
# Resizing the HUD means changing the size/scale of its layout objects, not its pictures.
# PG3D's in-match HUD may use NGUI (plain Transforms + UIWidget/UISprite scripts) rather
# than Unity UI (RectTransforms), so both are looked at. Two results: objects whose name
# or place looks like the in-match HUD, and an outline of every UI root (so the HUD can be
# found even when its names don't match). Nothing is changed.
_HUD_STRONG = ("hud", "ingame", "ingamegui", "weaponslot", "weaponslots", "healthbar", "armorbar", "hpbar",
               "ammo", "crosshair", "reticle", "killfeed", "minimap", "radar", "joystick", "gadget", "hotbar",
               "weaponpanel", "weaponbar", "gameplay", "battleui", "matchui")
_HUD_MID = ("weapon", "slot", "health", "armor", "armour", "hp", "ability", "skill", "button", "btn",
            "key", "hotkey", "reload", "jump", "fire", "shoot", "aim", "zoom", "timer", "score", "kill",
            "damage", "grenade", "pet", "potion", "consumable", "bar", "counter", "indicator", "pause",
            "gui", "map")
_HUD_NOT = ("shop", "store", "lobby", "menu", "bank", "profile", "clan", "friends", "settings", "lottery",
            "chest", "pass", "reward", "offer", "news", "craft", "armory", "avatar", "skin", "preview",
            "tutorial", "training", "loading", "cubic", "popup", "login", "register", "captcha", "otp",
            "comics", "account", "parental", "subscription", "faq", "support", "email", "consent")
_UI_ROOT_HINTS = ("ui", "gui", "hud", "canvas", "panel", "window", "root", "ingame", "controller", "screen")


def _hud_excluded(text):
    flat = re.sub(r"[^a-z0-9]", "", (text or "").lower())
    return any(w in flat for w in _HUD_NOT)


def hud_name_score(name):
    flat = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    words = set(_words(name))
    if not flat or _hud_excluded(name):
        return 0
    s = 0
    s += sum(3 for w in _HUD_STRONG if w in flat)
    s += sum(1 for w in _HUD_MID if w in words or (len(w) > 3 and w in flat))
    return s


def _pid(ptr):
    for k in ("m_PathID", "path_id"):
        v = getattr(ptr, k, None)
        if v is not None:
            return int(v)
    return 0


def _fid(ptr):
    return int(getattr(ptr, "m_FileID", getattr(ptr, "file_id", 0)) or 0)


def _widget_info(comps, monos, sprites):
    """Size and sprite of an NGUI widget or Unity UI image on this object (when the
    file keeps the script fields)."""
    info = {}
    for c in comps[:12]:
        ptr = getattr(c, "component", c)
        m = monos.get(_pid(ptr)) if _fid(ptr) == 0 else None
        if m is None:
            continue
        try:
            md = m.parse_as_object()
        except Exception:
            continue
        for k, out in (("mWidth", "w"), ("mHeight", "h"), ("mSpriteName", "sprite"), ("mDepth", "depth")):
            v = getattr(md, k, None)
            if v not in (None, "") and out not in info:
                info[out] = v
        sp = getattr(md, "m_Sprite", None)
        if sp is not None and _pid(sp) and "sprite" not in info:
            so = sprites.get(_pid(sp)) if _fid(sp) == 0 else None
            info["sprite"] = _name(so) if so is not None else f"(other file) #{_pid(sp)}"
    return info


def hud_scan(files, app: Path, progress=None, log=print, limit=4000):
    """Returns {"hits": [...], "roots": [...]}."""
    import UnityPy

    hits, roots_out = [], []
    total = max(1, len(files))
    for idx, p in enumerate(files, 1):
        if progress:
            progress(idx / total, f"Scanning HUD layout ({idx}/{total}, {len(hits)} found)…")
        try:
            env = UnityPy.load(str(p))
            env._pg3d_own_files = {id(o.assets_file) for o in env.objects}
        except Exception as e:
            log(f"HUD LOAD FAIL {p}: {e}")
            continue
        try:
            rel = str(Path(p).relative_to(app))
        except ValueError:
            rel = Path(p).name
        gos, tfs, monos, sprites = {}, {}, {}, {}
        for obj in _own_objects(env):
            t = obj.type.name
            if t == "GameObject":
                gos[int(obj.path_id)] = obj
            elif t in ("RectTransform", "Transform"):
                tfs[int(obj.path_id)] = obj
            elif t == "MonoBehaviour":
                monos[int(obj.path_id)] = obj
            elif t == "Sprite":
                sprites[int(obj.path_id)] = obj
        if not gos or not tfs or not monos:
            del env
            continue  # no scripted objects: models, textures, sounds
        names, comps = {}, {}
        for pid, obj in gos.items():
            try:
                d = obj.parse_as_object()
                names[pid] = str(getattr(d, "m_Name", "") or "")
                comps[pid] = list(getattr(d, "m_Component", None) or [])
            except Exception:
                continue
        tf, of_go, children = {}, {}, {}
        for pid, obj in tfs.items():
            try:
                d = obj.parse_as_object()
            except Exception:
                continue
            go, father = _pid(d.m_GameObject), _pid(getattr(d, "m_Father", None))
            tf[pid] = (go, father)
            of_go[go] = (pid, d, obj.type.name == "RectTransform")
            children.setdefault(father, []).append(pid)

        def path_of(t):
            parts, seen = [], set()
            while t and t in tf and t not in seen and len(parts) < 14:
                seen.add(t)
                g, father = tf[t]
                parts.append(names.get(g, "?"))
                t = father
            return "/".join(reversed(parts))

        def count(t, depth=0):
            return 1 + sum(count(c, depth + 1) for c in children.get(t, [])) if depth < 30 else 1

        # UI roots: Unity UI anywhere below, or a UI-like name.
        for t, (go, father) in tf.items():
            if father in tf:
                continue
            name = names.get(go, "")
            is_ui = of_go.get(go, (0, 0, False))[2] or any(h in name.lower() for h in _UI_ROOT_HINTS)
            n = count(t)
            if not is_ui or n < 4 or _hud_excluded(name):
                continue
            kids = [names.get(tf[c][0], "?") for c in children.get(t, [])[:30] if c in tf]
            roots_out.append({"file": rel, "root": name, "objects": n, "children": kids,
                              "score": hud_name_score(name) + sum(hud_name_score(k) for k in kids)})

        for go, (t, d, is_rect) in of_go.items():
            name = names.get(go, "")
            path = path_of(t)
            own, in_hud = hud_name_score(name), hud_name_score(path)
            if _hud_excluded(path) or (own <= 0 and in_hud < 3):
                continue
            try:
                sc = d.m_LocalScale
                scale = [round(float(sc.x), 3), round(float(sc.y), 3)]
            except Exception:
                scale = None
            size = None
            if is_rect:
                try:
                    size = [round(float(d.m_SizeDelta.x), 1), round(float(d.m_SizeDelta.y), 1)]
                except Exception:
                    pass
            w = _widget_info(comps.get(go, []), monos, sprites)
            if size is None and "w" in w and "h" in w:
                size = [w["w"], w["h"]]
            hits.append({"name": name, "path": path, "score": own + in_hud // 2, "size": size, "scale": scale,
                         "kind": "uGUI" if is_rect else ("NGUI" if w else "Transform"),
                         "sprites": [w["sprite"]] if w.get("sprite") else [],
                         "source_file": str(p), "relative_file": rel, "path_id": int(go)})
        del env
    hits.sort(key=lambda h: (-h["score"], h["relative_file"], h["path"]))
    roots_out.sort(key=lambda r: (-r["score"], -r["objects"]))
    return {"hits": hits[:limit], "roots": roots_out[:400]}


def hud_report(result, build=""):
    if isinstance(result, list):  # older scans
        result = {"hits": result, "roots": []}
    hits, roots = result.get("hits", []), result.get("roots", [])
    lines = [f"PG3D HUD layout scan v2 · build {build} · {len(hits)} objects · {len(roots)} UI roots", ""]
    by_file = {}
    for h in hits:
        by_file.setdefault(h["relative_file"], []).append(h)
    for rel, items in sorted(by_file.items(), key=lambda kv: -max(h["score"] for h in kv[1]))[:60]:
        lines.append(f"== {rel} ({len(items)})")
        for h in sorted(items, key=lambda h: (-h["score"], h["path"]))[:80]:
            size = f"{h['size'][0]:g}x{h['size'][1]:g}" if h.get("size") else "?"
            scale = f"{h['scale'][0]:g}" if h.get("scale") else "?"
            spr = f"  sprite: {', '.join(map(str, h['sprites'][:3]))}" if h.get("sprites") else ""
            lines.append(f"  [{h['score']}] {h['path']}  {h.get('kind', '')} size {size} scale {scale}  #{h['path_id']}{spr}")
        lines.append("")
    lines.append("== UI roots (every file)")
    for r in roots[:150]:
        kids = ", ".join(r["children"][:25])
        lines.append(f"  [{r['score']}] {r['file']} :: {r['root']} ({r['objects']} objects): {kids}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------- HUD groups
# PG3D's in-match HUD is the NGUI prefab "InGameGUI" in resources.assets. Its pieces sit in
# screen-corner anchor groups (TopLeftAnchor, WeaponTogglesForCharacter, BottomRightAnchor,
# ...). Scaling one group's Transform resizes that whole block around its corner.
HUD_GROUP_PARENTS = ("InGameInterface/InterfaceContainer", "JoystikPanel/JoystickContainer")
HUD_GROUPS_V = 2
# PG3D sets these containers' own scale at runtime, so their parts are scaled instead.
HUD_SCALE_CHILDREN = {"weapontogglesforcharacter"}
HUD_GROUP_LABELS = {
    "topleftanchor": "Top left: health, armor, pet",
    "toprightanchor": "Top right",
    "topanchor": "Top center",
    "centeranchorscope": "Center: crosshair and ammo",
    "centeranchor": "Center",
    "weapontogglesforcharacter": "Weapon bar",
    "bottomleftanchor": "Bottom left buttons",
    "bottomrightanchor": "Bottom right: gadgets and abilities",
    "bottomanchor": "Bottom center",
    "leftanchor": "Left side",
    "rightanchor": "Right side",
}


def hud_groups(path, log=print):
    """The anchor groups of the in-match HUD: [{path_id (Transform), name, label, path,
    scale [x,y,z], children [...]}]."""
    import UnityPy

    env = UnityPy.load(str(path))
    names, tf, children = {}, {}, {}
    objs = list(env.objects)
    for obj in objs:
        if obj.type.name == "GameObject":
            try:
                names[int(obj.path_id)] = str(obj.parse_as_object().m_Name or "")
            except Exception:
                pass
    for obj in objs:
        if obj.type.name not in ("Transform", "RectTransform"):
            continue
        try:
            d = obj.parse_as_object()
        except Exception:
            continue
        pid, go, father = int(obj.path_id), _pid(d.m_GameObject), _pid(getattr(d, "m_Father", None))
        try:
            sc = d.m_LocalScale
            scale = [float(sc.x), float(sc.y), float(sc.z)]
        except Exception:
            scale = [1.0, 1.0, 1.0]
        tf[pid] = (go, father, scale)
        children.setdefault(father, []).append(pid)

    def name(t):
        return names.get(tf[t][0], "") if t in tf else ""

    def path_of(t):
        parts, seen = [], set()
        while t in tf and t not in seen and len(parts) < 16:
            seen.add(t)
            parts.append(name(t))
            t = tf[t][1]
        return "/".join(reversed(parts))

    roots = [t for t, (go, father, _) in tf.items() if father not in tf and name(t) == "InGameGUI"]
    out = []
    for root in roots:
        stack = [root]
        while stack:
            t = stack.pop()
            p = path_of(t)
            stack.extend(children.get(t, []))
            if not any(p.endswith(parent) for parent in HUD_GROUP_PARENTS):
                continue
            for c in children.get(t, []):
                n = name(c)
                if not n or "(off)" in n.lower():
                    continue
                key = re.sub(r"[^a-z]", "", n.lower())
                kids = [name(k) for k in children.get(c, [])[:10]]
                out.append({"path_id": c, "name": n, "label": HUD_GROUP_LABELS.get(key, n), "path": path_of(c),
                            "scale": tf[c][2], "children": [k for k in kids if k],
                            "mode": "children" if key in HUD_SCALE_CHILDREN else "self",
                            "child_ids": [k for k in children.get(c, []) if k in tf]})
    order = list(HUD_GROUP_LABELS)
    out.sort(key=lambda g: (order.index(re.sub(r"[^a-z]", "", g["name"].lower()))
                            if re.sub(r"[^a-z]", "", g["name"].lower()) in order else len(order), g["path"]))
    log(f"HUD groups: {len(out)} in {Path(path).name}")
    return out


def scale_transform(reader, factor, move=False):
    """Multiply a Transform's x/y scale (z kept) in place, from the file's own value.
    move=True also scales its position: the same as scaling its parent by factor."""
    tree = reader.read_typetree()
    s = tree["m_LocalScale"]
    s["x"] = float(s["x"]) * factor
    s["y"] = float(s["y"]) * factor
    if move:
        p = tree["m_LocalPosition"]
        p["x"] = float(p["x"]) * factor
        p["y"] = float(p["y"]) * factor
    reader.save_typetree(tree)


# ----------------------------------------------------------------------------- sky v2
# Where PG3D's sky really is, found the way the game uses it (not by texture name, which
# also hit weapons like "SkyHammer"):
#  - skybox materials: each map's lighting settings (RenderSettings.m_SkyboxMaterial)
#    and materials with a Skybox shader -> their face / cubemap / panorama textures;
#  - sky domes: big mesh objects named like a sky (Sky, SkyDome, SkySphere, ...) -> the
#    texture on their material plus the mesh itself, so a picture can be projected
#    through the mesh's own UVs (no stretching, no seam).
SKY_SCAN_V = 3  # 2: textures/meshes recorded in the file they really live in; stricter names
# 3: a dome's Transform (rotation/scale) is kept, so a scaled-up small sphere counts too
_SKY_TOKENS = {"sky", "skies", "skydome", "skysphere", "skybox", "skymesh", "skyplane"}


def _sky_go_name(name):
    """Objects named like the sky itself ("Sky", "sky_01", "SkyDome", "CP_skydome",
    "skybox_13_cloud"), not "skyscraper_1" or plain cloud layers."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "")
    tokens = [t for t in re.split(r"[^a-z0-9]+", spaced.lower()) if t]
    tokens = [re.sub(r"\d+$", "", t) for t in tokens]
    joined = "".join(tokens)
    return any(t in _SKY_TOKENS for t in tokens) or joined in ("skydome", "skysphere", "skybox")


def _deref(ptr):
    try:
        return ptr.deref() if ptr is not None and _pptr_ids(ptr)[1] else None
    except Exception:
        return None


def _mesh_extent(mesh_reader):
    try:
        e = mesh_reader.parse_as_object().m_LocalAABB.m_Extent
        return max(abs(float(e.x)), abs(float(e.y)), abs(float(e.z)))
    except Exception:
        return 0.0


def sky_deep_scan(files, app: Path, progress=None, log=print, thumbs=None):
    """[{source_file, relative_file, path_id, name, type, width, height, face, kind,
    where, mesh_id}] - kind: skybox | dome. thumbs(dir) saves small previews."""
    import UnityPy

    found = {}
    total = max(1, len(files))
    for idx, p in enumerate(files, 1):
        if progress:
            progress(idx / total, f"Looking for the sky ({idx}/{total}, {len(found)} found)…")
        try:
            env = UnityPy.load(str(p))
        except Exception as e:
            log(f"SKY LOAD FAIL {p}: {e}")
            continue
        objs = list(env.objects)
        own = {id(o.assets_file) for o in objs}  # before following references loads other files
        types = {o.type.name for o in objs}
        if "Material" not in types and "RenderSettings" not in types:
            del env
            continue
        try:
            rel = str(Path(p).relative_to(app))
        except ValueError:
            rel = Path(p).name
        hits = []  # (texture reader, face, kind, where, mesh reader, matrix)

        def material_textures(mat_reader, kind, where, mesh=None, matrix=None):
            try:
                mat = mat_reader.parse_as_object()
            except Exception:
                return
            shader = ""
            sh = _deref(getattr(mat, "m_Shader", None))
            if sh is not None:
                shader = _name(sh) or ""
            for key, env_ in _tex_env_items(mat):
                slot = SKYBOX_MATERIAL_SLOTS.get(key) if kind == "skybox" else ("dome" if key == "_MainTex" else None)
                if not slot:
                    continue
                tex = _deref(getattr(env_, "m_Texture", None))
                if tex is not None and tex.type.name in ("Texture2D", "Cubemap"):
                    hits.append((tex, slot, kind, f"{where} · {getattr(mat, 'm_Name', '')} ({shader})", mesh, matrix))

        for o in objs:
            t = o.type.name
            try:
                if t == "RenderSettings":
                    m = _deref(getattr(o.parse_as_object(), "m_SkyboxMaterial", None))
                    if m is not None:
                        material_textures(m, "skybox", "map skybox")
                elif t == "Material":
                    mat = o.parse_as_object()
                    sh = _deref(getattr(mat, "m_Shader", None))
                    if sh is not None and "skybox" in (_name(sh) or "").lower():
                        material_textures(o, "skybox", "skybox material")
                elif t == "GameObject":
                    go = o.parse_as_object()
                    if not _sky_go_name(getattr(go, "m_Name", "")):
                        continue
                    mesh, mats, tr = None, [], None
                    for c in getattr(go, "m_Component", None) or []:
                        comp = _deref(getattr(c, "component", c))
                        if comp is None:
                            continue
                        if comp.type.name == "MeshFilter":
                            mesh = _deref(getattr(comp.parse_as_object(), "m_Mesh", None))
                        elif comp.type.name == "MeshRenderer":
                            mats = [_deref(m) for m in (getattr(comp.parse_as_object(), "m_Materials", None) or [])]
                        elif comp.type.name in ("Transform", "RectTransform"):
                            tr = comp
                    if mesh is None:
                        continue
                    try:
                        matrix = _transform_matrix(tr) if tr is not None else None
                    except Exception:
                        matrix = None
                    scale = float(abs(matrix).sum(axis=0).max()) if matrix is not None else 1.0
                    if _mesh_extent(mesh) * scale < 30:
                        continue  # small objects named "sky..." aren't the sky
                    for m in mats:
                        if m is not None:
                            material_textures(m, "dome", f"sky dome {go.m_Name}", mesh, matrix)
            except Exception as e:
                log(f"SKY OBJ FAIL {p} {getattr(o, 'path_id', '?')}: {e}")

        def home(reader):
            """(physical file, inner file name) of an object: a scene's sky texture often
            lives in another file (level5 -> sharedassets5.assets)."""
            af = reader.assets_file
            cab = str(getattr(af, "name", "") or "")
            if id(af) in own:
                return str(p), cab
            cand = Path(p).parent / cab
            return (str(cand), cab) if cab and cand.is_file() else (None, cab)

        for tex, slot, kind, where, mesh, matrix in hits:
            tex_file, tex_cab = home(tex)
            if tex_file is None:
                continue  # in a bundle we can't place
            key = (tex_file, tex_cab, int(tex.path_id))
            if key in found:
                continue
            try:
                data = tex.parse_as_object()
                w, h = int(data.m_Width), int(data.m_Height)
            except Exception:
                continue
            if tex.type.name == "Texture2D" and min(w, h) < 64:
                continue  # gradients / 16x16 fillers, not a picture of a sky
            mesh_file, mesh_cab = home(mesh) if mesh is not None else (None, None)
            try:
                rel = str(Path(tex_file).relative_to(app))
            except ValueError:
                rel = Path(tex_file).name
            typ = tex.type.name
            face = "cube" if typ == "Cubemap" else ("panorama" if slot in ("panorama", "cube") else slot)
            item = {"source_file": tex_file, "relative_file": rel, "path_id": int(tex.path_id), "name": _name(tex),
                    "type": typ, "width": w, "height": h, "face": face, "kind": kind, "where": where, "cab": tex_cab,
                    "mesh_id": int(mesh.path_id) if mesh is not None and mesh_file else None,
                    "mesh_file": mesh_file, "mesh_cab": mesh_cab,
                    "matrix": [[float(v) for v in row] for row in matrix] if matrix is not None and mesh_file else None}
            if thumbs is not None and typ == "Texture2D":
                try:
                    im = data.image.convert("RGB")
                    im.thumbnail((192, 192))
                    item["_thumb"] = im
                except Exception:
                    pass
            found[key] = item
        del env
    return list(found.values())


def _transform_matrix(tr_reader):
    """Rotation and scale (3x3, no position) from an object's own space to the world:
    a sky sphere is often a small mesh scaled up, turned or mirrored by its Transform."""
    import numpy as np

    m = np.eye(3)
    seen = set()
    while tr_reader is not None and int(tr_reader.path_id) not in seen:
        seen.add(int(tr_reader.path_id))
        tr = tr_reader.parse_as_object()
        q, s = tr.m_LocalRotation, tr.m_LocalScale
        x, y, z, w = float(q.x), float(q.y), float(q.z), float(q.w)
        n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
        x, y, z, w = x / n, y / n, z / n, w / n
        r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        m = r @ np.diag([float(s.x), float(s.y), float(s.z)]) @ m
        tr_reader = _deref(getattr(tr, "m_Father", None))
    return m


_GENERIC_MESHES = {"", "sphere", "cube", "quad", "plane", "cylinder", "capsule", "default"}
_GENERIC_SHADERS = {"", "standard", "unlit/texture", "mobile/diffuse", "diffuse", "legacy shaders/diffuse",
                    "mobile/unlit (supports lightmap)", "unlit/transparent", "sprites/default", "ui/default"}


def _shader_name(sh):
    name = _name(sh)
    if name:
        return name
    try:
        return str(sh.parse_as_object().m_ParsedForm.m_Name or "")
    except Exception:
        return ""


def _scan_renderers(files, want, found, progress=None, label="", log=print):
    """Walk every MeshRenderer in these files. want(mat) -> slot or None decides by
    material ({"shader", "name", "texs": [(slot, tex reader, file, cab)]}); for wanted
    ones found(info) gets {tex, tex_file, tex_cab, slot, shader, material, object,
    mesh, mesh_file, mesh_cab, mesh_name, matrix, extent}."""
    import UnityPy
    import numpy as np

    total = max(1, len(files))
    for idx, p in enumerate(files, 1):
        if progress:
            progress(idx / total, f"{label} ({idx}/{total})…")
        try:
            env = UnityPy.load(str(p))
        except Exception:
            continue
        objs = list(env.objects)
        if not any(o.type.name == "MeshRenderer" for o in objs):
            del env
            continue
        own = {id(o.assets_file) for o in objs}

        def home(reader):
            af = reader.assets_file
            name = str(getattr(af, "name", "") or "")
            if id(af) in own:
                return str(p), name
            cand = Path(p).parent / name
            return (str(cand), name) if name and cand.is_file() else (None, name)

        mats, shaders = {}, {}

        def shader_of(mi):
            """Shader names are read only for wanted materials (shaders are big)."""
            if "shader_done" not in mi:
                mi["shader_done"] = True
                sh = _deref(mi.get("shader_ref"))
                if sh is not None:
                    sk = (id(sh.assets_file), int(sh.path_id))
                    if sk not in shaders:
                        shaders[sk] = _shader_name(sh)
                    mi["shader"] = shaders[sk]
            return mi["shader"]

        def mat_info(m):
            key = (id(m.assets_file), int(m.path_id))
            if key not in mats:
                info = {"shader": "", "name": "", "texs": []}
                try:
                    mat = m.parse_as_object()
                    info["name"] = str(getattr(mat, "m_Name", "") or "")
                    info["shader_ref"] = getattr(mat, "m_Shader", None)
                    for slot, tenv in _tex_env_items(mat):
                        tex = _deref(getattr(tenv, "m_Texture", None))
                        if tex is not None:
                            f, c = home(tex)
                            info["texs"].append((slot, tex, f, c))
                except Exception:
                    pass
                mats[key] = info
            return mats[key]

        for o in objs:
            if o.type.name != "MeshRenderer":
                continue
            try:
                rend = o.parse_as_object()
                hits = []
                for m in (getattr(rend, "m_Materials", None) or []):
                    m = _deref(m)
                    if m is None:
                        continue
                    mi = mat_info(m)
                    slot = want(mi)
                    if slot:
                        hits.append((mi, slot))
                if not hits:
                    continue
                go_r = _deref(getattr(rend, "m_GameObject", None))
                if go_r is None:
                    continue
                go = go_r.parse_as_object()
                mesh, tr = None, None
                for c in getattr(go, "m_Component", None) or []:
                    comp = _deref(getattr(c, "component", c))
                    if comp is None:
                        continue
                    if comp.type.name == "MeshFilter":
                        mesh = _deref(getattr(comp.parse_as_object(), "m_Mesh", None))
                    elif comp.type.name in ("Transform", "RectTransform"):
                        tr = comp
                if mesh is None:
                    continue
                mesh_file, mesh_cab = home(mesh)
                if mesh_file is None:
                    continue
                matrix = _transform_matrix(tr) if tr is not None else np.eye(3)
                extent = _mesh_extent(mesh) * float(np.abs(matrix).sum(axis=0).max())
                for mi, slot in hits:
                    shader_of(mi)
                    for tslot, tex, tf, tc in mi["texs"]:
                        if tslot != slot:
                            continue
                        found({"tex": tex, "tex_file": tf, "tex_cab": tc, "slot": slot, "shader": mi["shader"],
                               "material": mi["name"], "object": str(getattr(go, "m_Name", "") or ""),
                               "mesh": mesh, "mesh_file": mesh_file, "mesh_cab": mesh_cab,
                               "mesh_name": _name(mesh), "matrix": matrix, "extent": extent, "file": str(p)})
            except Exception as e:
                log(f"SKY RENDERER skip {p} {o.path_id}: {e}")
        del env


def find_texture_mesh(files, tex_file, path_id, cab=None, progress=None, log=print):
    """The mesh that draws one texture in the game (the sky sphere behind a sky texture
    found by the sky test): {mesh_id, mesh_file, mesh_cab, matrix, object, extent,
    mesh_name, shader, slot} or None. The biggest one wins when several objects use it."""
    best = {}

    def want(mi):
        for slot, tex, f, c in mi["texs"]:
            if int(tex.path_id) == int(path_id) and f == str(tex_file) and (not cab or c == cab):
                return slot
        return None

    def found(info):
        if int(info["tex"].path_id) != int(path_id):
            return
        if not best or info["extent"] > best["extent"]:
            best.update({"mesh_id": int(info["mesh"].path_id), "mesh_file": info["mesh_file"],
                         "mesh_cab": info["mesh_cab"], "matrix": [[float(v) for v in row] for row in info["matrix"]],
                         "object": info["object"], "extent": info["extent"], "mesh_name": info["mesh_name"],
                         "shader": info["shader"], "slot": info["slot"]})
    _scan_renderers(files, want, found, progress, "Looking for the shape of your sky", log)
    return best or None


def find_sky_family(files, ref, app, progress=None, log=print):
    """The same kind of sky in every other map: objects drawn like the picked one (same
    sky mesh, or same object name, or same sky shader) and about as big. Returns sky
    targets (each with its own mesh and Transform)."""
    mesh_name = str(ref.get("mesh_name") or "")
    obj = str(ref.get("object") or "")
    shader = str(ref.get("shader") or "")
    slot = ref.get("slot") or "_MainTex"
    min_extent = 0.3 * float(ref.get("extent") or 0)
    out = {}

    def want(mi):
        return slot if any(s == slot for s, *_ in mi["texs"]) else None

    def found(info):
        if info["tex_file"] is None or info["extent"] < min_extent:
            return
        same_mesh = mesh_name.lower() not in _GENERIC_MESHES and info["mesh_name"] == mesh_name
        same_obj = bool(obj) and info["object"] == obj
        same_shader = shader.lower() not in _GENERIC_SHADERS and info["shader"] == shader
        if not (same_mesh or same_obj or same_shader):
            return
        tex = info["tex"]
        key = (info["tex_file"], info["tex_cab"], int(tex.path_id))
        if key in out:
            return
        try:
            if tex.type.name != "Texture2D":
                return
            data = tex.parse_as_object()
            w, h = int(data.m_Width), int(data.m_Height)
        except Exception:
            return
        if min(w, h) < 64:
            return
        try:
            rel = str(Path(info["tex_file"]).relative_to(app))
        except ValueError:
            rel = Path(info["tex_file"]).name
        out[key] = {"source_file": info["tex_file"], "relative_file": rel, "path_id": int(tex.path_id),
                    "name": _name(tex), "type": "Texture2D", "face": "panorama", "width": w, "height": h,
                    "kind": "dome", "cab": info["tex_cab"], "mesh_id": int(info["mesh"].path_id),
                    "mesh_file": info["mesh_file"], "mesh_cab": info["mesh_cab"],
                    "matrix": [[float(v) for v in row] for row in info["matrix"]], "mirror": True,
                    "where": f"{info['object']} · {info['material']}"}
    _scan_renderers(files, want, found, progress, "Looking for the same sky in the other maps", log)
    return list(out.values())


def find_material_family(files, tex_file, path_id, cab, app, progress=None, log=print):
    """For a sky that no object in the map draws (a skybox material): the material that
    uses the picked texture, and every texture in the same slot of materials with the
    same (non-generic) shader in all files. Returns (ref, targets)."""
    import UnityPy

    ref = {}
    out = {}
    own_first = sorted(files, key=lambda f: str(f) != str(tex_file))

    def walk(file_list, visit, label, lo, hi):
        total = max(1, len(file_list))
        for idx, p in enumerate(file_list, 1):
            if progress:
                progress(lo + (hi - lo) * idx / total, f"{label} ({idx}/{total})…")
            try:
                env = UnityPy.load(str(p))
            except Exception:
                continue
            objs = list(env.objects)
            if not any(o.type.name == "Material" for o in objs):
                del env
                continue
            own = {id(o.assets_file) for o in objs}
            shaders = {}

            def home(reader):
                af = reader.assets_file
                name = str(getattr(af, "name", "") or "")
                if id(af) in own:
                    return str(p), name
                cand = Path(p).parent / name
                return (str(cand), name) if name and cand.is_file() else (None, name)

            def shader_name(mat):
                sh = _deref(getattr(mat, "m_Shader", None))
                if sh is None:
                    return ""
                k = (id(sh.assets_file), int(sh.path_id))
                if k not in shaders:
                    shaders[k] = _shader_name(sh)
                return shaders[k]
            for o in objs:
                if o.type.name != "Material":
                    continue
                try:
                    if visit(o.parse_as_object(), home, shader_name, p) is False:
                        del env
                        return
                except Exception as e:
                    log(f"SKY MATERIAL skip {p} {o.path_id}: {e}")
            del env

    def find_ref(mat, home, shader_name, p):
        for slot, tenv in _tex_env_items(mat):
            tex = _deref(getattr(tenv, "m_Texture", None))
            if tex is not None and int(tex.path_id) == int(path_id):
                f, c = home(tex)
                if f == str(tex_file) and (not cab or c == cab):
                    ref.update(shader=shader_name(mat), slot=slot, material=str(getattr(mat, "m_Name", "") or ""))
                    return False
        return None
    walk(own_first[:1], find_ref, "Looking for your sky's material", 0.0, 0.05)
    if not ref:
        walk(own_first[1:], find_ref, "Looking for your sky's material", 0.05, 0.5)
    if not ref:
        return {}, []
    log(f"Sky material: {ref}")
    if str(ref.get("shader") or "").lower() in _GENERIC_SHADERS:
        return ref, []

    def collect(mat, home, shader_name, p):
        if shader_name(mat) != ref["shader"]:
            return None
        for slot, tenv in _tex_env_items(mat):
            if slot != ref["slot"]:
                continue
            tex = _deref(getattr(tenv, "m_Texture", None))
            if tex is None or tex.type.name != "Texture2D":
                continue
            f, c = home(tex)
            if f is None or (f, c, int(tex.path_id)) in out:
                continue
            try:
                data = tex.parse_as_object()
                w, h = int(data.m_Width), int(data.m_Height)
            except Exception:
                continue
            if min(w, h) < 64:
                continue
            try:
                rel = str(Path(f).relative_to(app))
            except ValueError:
                rel = Path(f).name
            out[(f, c, int(tex.path_id))] = {
                "source_file": f, "relative_file": rel, "path_id": int(tex.path_id), "name": _name(tex),
                "type": "Texture2D", "face": "panorama", "width": w, "height": h, "kind": "name", "cab": c,
                "mesh_id": None, "mesh_file": None, "mesh_cab": None, "matrix": None, "mirror": True,
                "where": f"same sky shader · {getattr(mat, 'm_Name', '')}"}
        return None
    walk(files, collect, "Looking for the same sky in the other maps", 0.5, 1.0)
    return ref, list(out.values())


def calm_poles(img: Image.Image, frac=0.07) -> Image.Image:
    """A picture wrapped round a sky sphere meets itself at the very top and bottom,
    where every column squeezes into one point: differing colours there show as a
    pinwheel of wedges. The top and bottom rows fade into their average colour."""
    import numpy as np

    a = np.asarray(img.convert("RGBA"), dtype=np.float32)
    h = a.shape[0]
    n = max(2, int(h * frac))
    for rows, avg_rows in ((range(n), a[:max(1, n // 2)]), (range(h - n, h), a[h - max(1, n // 2):])):
        avg = avg_rows.reshape(-1, 4).mean(axis=0)
        for i, y in enumerate(rows):
            d = (y + 1) / n if y < n else (h - y) / n  # 0 at the pole -> 1 at the edge of the cap
            w = max(0.0, min(1.0, d)) ** 1.5
            a[y] = a[y] * w + avg * (1 - w)
    return Image.fromarray(np.clip(a + 0.5, 0, 255).astype(np.uint8), "RGBA")


def _mesh_arrays(mesh_reader):
    """Vertex positions (N,3), UVs (N,2) and triangles (M,3) of a Unity mesh."""
    import numpy as np
    from UnityPy.helpers.MeshHelper import MeshHandler

    h = MeshHandler(mesh_reader.parse_as_object())
    h.process()
    pos = np.asarray(h.m_Vertices, dtype=np.float64).reshape(-1, 3)
    uv = np.asarray(h.m_UV0, dtype=np.float64).reshape(-1, 2)
    if h.m_UV0 is None or not len(uv):
        raise ValueError("the sky mesh has no UVs")
    tris = []
    for sub in h.get_triangles():
        for f in sub:
            if len(f) == 3:
                tris.append(f)
            elif len(f) == 4:  # quads
                tris += [(f[0], f[1], f[2]), (f[0], f[2], f[3])]
    if not tris:
        raise ValueError("the sky mesh has no triangles")
    return pos, uv, np.asarray(tris, dtype=np.int64).reshape(-1, 3)


def project_on_mesh(pano: Image.Image, pos, uv, tris, size, yaw=0.0, mirror=False, flip=False, matrix=None):
    """Paint an equirectangular picture onto a sky mesh's UV layout: every texel gets the
    picture's colour in the direction its spot on the mesh is seen from the centre.
    matrix: the object's rotation/scale, so directions are the world's (up is up)."""
    import numpy as np

    if matrix is not None:
        pos = np.asarray(pos, dtype=np.float64) @ np.asarray(matrix, dtype=np.float64).T

    W, H = size
    src = np.asarray(pano.convert("RGB"), dtype=np.uint8)
    sh, sw = src.shape[:2]
    out = np.zeros((H, W, 3), dtype=np.uint8)
    filled = np.zeros((H, W), dtype=bool)
    centre = (pos.min(0) + pos.max(0)) / 2.0
    d = pos - centre
    px = uv[:, 0] * W - 0.5
    py = (1.0 - uv[:, 1]) * H - 0.5  # Unity UV origin is bottom-left
    yaw_r = math.radians(yaw)
    for a, b, c in tris:
        xs, ys = px[[a, b, c]], py[[a, b, c]]
        x0, x1 = int(max(0, math.floor(xs.min()))), int(min(W - 1, math.ceil(xs.max())))
        y0, y1 = int(max(0, math.floor(ys.min()))), int(min(H - 1, math.ceil(ys.max())))
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        den = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
        if abs(den) < 1e-12:
            continue
        l0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / den
        l1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / den
        l2 = 1.0 - l0 - l1
        inside = (l0 >= -1e-3) & (l1 >= -1e-3) & (l2 >= -1e-3)
        if not inside.any():
            continue
        v = l0[..., None] * d[a] + l1[..., None] * d[b] + l2[..., None] * d[c]
        v = v[inside]
        lon = np.arctan2(v[:, 0], v[:, 2]) + yaw_r
        if mirror:
            lon = -lon
        lat = np.arctan2(v[:, 1], np.hypot(v[:, 0], v[:, 2]))
        if flip:
            lat = -lat
        u = ((lon / (2 * math.pi)) + 0.5) % 1.0
        t = 0.5 - lat / math.pi
        sx = np.clip((u * sw).astype(np.int64), 0, sw - 1)
        sy = np.clip((t * sh).astype(np.int64), 0, sh - 1)
        yy, xx = gy[inside], gx[inside]
        out[yy, xx] = src[sy, sx]
        filled[yy, xx] = True
    if not filled.all():
        out = _dilate_fill(out, filled)
    return Image.fromarray(out, "RGB").convert("RGBA")


def _dilate_fill(out, filled, rounds=64):
    """Texels no triangle covers (between the thin triangles at the top of a sky
    sphere, and around the UV islands) take the colour of their covered neighbours.
    The game's texture filtering reads them, and left black (or blurred with black)
    they showed as dark wedges around the top of the sky."""
    import numpy as np

    img = out.astype(np.float32)
    f = filled.copy()
    if not f.any():
        return out
    for _ in range(rounds):
        if f.all():
            break
        acc = np.zeros_like(img)
        cnt = np.zeros(f.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sf = np.roll(f, (dy, dx), (0, 1))
            acc += np.roll(img, (dy, dx), (0, 1)) * sf[..., None]
            cnt += sf
        new = (~f) & (cnt > 0)
        img[new] = acc[new] / cnt[new][:, None]
        f |= new
    if not f.all():
        img[~f] = img[f].mean(axis=0)
    return np.clip(img + 0.5, 0, 255).astype(np.uint8)


def equirect_from(img: Image.Image, yaw=0.0) -> Image.Image:
    """The picture as a 2:1 wrap-around panorama (photos are made seamless first)."""
    pano = as_panorama(img)
    if yaw:
        shift = int(round(yaw / 360.0 * pano.width)) % pano.width
        pano = ImageChops.offset(pano.convert("RGBA"), -shift, 0)
    return pano


def sky_test_image(num, size, font_path=None):
    """A test pattern for finding which texture is the sky: a colour of its own and the
    number repeated in a grid, so any part of it seen in game shows the number."""
    from PIL import ImageDraw, ImageFont

    w, h = max(1, int(size[0])), max(1, int(size[1]))
    hue = (num * 0.61803398875) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.85)
    img = Image.new("RGBA", (w, h), (int(r * 255), int(g * 255), int(b * 255), 255))
    d = ImageDraw.Draw(img)
    cols = 3 if w >= 96 else 1
    rows = max(1, round(cols * h / max(1, w))) if cols > 1 else 1
    cell_w, cell_h = w / cols, h / rows
    text = str(num)
    px = max(8, int(min(cell_w / (0.62 * len(text) + 0.4), cell_h * 0.8)))
    try:
        font = ImageFont.truetype(str(font_path), px) if font_path else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    stroke = max(1, px // 12)
    if h >= 64 and rows < 2:
        rows = 2
        cell_h = h / rows
        px = max(8, int(min(cell_w / (0.62 * len(text) + 0.4), cell_h * 0.8)))
        try:
            font = ImageFont.truetype(str(font_path), px) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        stroke = max(1, px // 12)
    # Every other row is drawn mirrored: a sky seen from inside shows the picture
    # backwards, so one of the two rows always reads the right way round.
    cell = Image.new("RGBA", (max(1, int(cell_w)), max(1, int(cell_h))), (0, 0, 0, 0))
    ImageDraw.Draw(cell).text((cell.width / 2, cell.height / 2), text, font=font, fill=(255, 255, 255, 255),
                              anchor="mm", stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    flipped = cell.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    for i in range(cols):
        for j in range(rows):
            img.alpha_composite(flipped if j % 2 else cell, (int(i * cell_w), int(j * cell_h)))
    return img


def sky_test_colour(num):
    r, g, b = colorsys.hsv_to_rgb((num * 0.61803398875) % 1.0, 0.75, 0.85)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))
