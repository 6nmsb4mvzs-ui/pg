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


_LOADING_HINTS = {"map", "maps", "preview", "background", "bg", "lobby", "menu", "art", "banner", "arena", "level",
                  "mode", "event", "season", "poster", "wallpaper", "screen"}
_NOT_LOADING = {"reload", "reloading", "weapon", "gun", "skin", "icon", "button", "atlas", "font", "normal", "mask",
                "lightmap", "shadowmask", "noise", "ramp", "lut", "sprite", "sheet", "anim", "fx", "particle"}


def is_loading_candidate(name: str, w: int, h: int) -> bool:
    """Screen-sized art that may be shown while a map loads (map previews and the
    like). Offered in the picker, not ticked by default."""
    words = _words(name)
    return (w >= 1024 and 1.45 <= w / max(1, h) <= 2.1 and bool(words & _LOADING_HINTS)
            and not words & _NOT_LOADING)


_SKY_WORD = re.compile(r"^(sky|skies|skybox|skydome|skysphere|skymap|skytex)\d*$")


_SKY_VOCAB = {"night", "day", "dawn", "dusk", "sunset", "sunrise", "evening", "morning", "cloud", "clouds",
              "cloudy", "space", "stars", "star", "galaxy", "blue", "dark", "red", "orange", "purple", "main",
              "front", "back", "left", "right", "up", "down", "top", "bottom", "ft", "bk", "lf", "rt", "dn",
              "px", "nx", "py", "ny", "pz", "nz", "pos", "neg", "posx", "negx", "posy", "negy", "posz", "negz",
              "x", "y", "z", "tex", "texture", "hdr", "hdri", "cube", "cubemap", "panorama", "pano", "box",
              "dome", "map", "bg", "background", "lowres", "hd", "lod", "new", "old", "copy"}


def is_sky_name(name: str, extra=()) -> bool:
    """A texture named only in sky terms: "Skybox_Front", "sky_03", "NightSky_up".
    "SkyHammer_diffuse" or "Weapon_SkyFall" carry other words and are not skies."""
    words = _words(name)
    if not any(_SKY_WORD.match(x) for x in words):
        return False
    return all(_SKY_WORD.match(x) or x.isdigit() or x in _SKY_VOCAB or x in extra for x in words)


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
                    "name": name, "width": w, "height": h, "suggested": sure, "_thumb": thumb})
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
