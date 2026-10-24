#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, quote_plus
from urllib.request import Request, urlopen

# Hide from the Dock before PyObjC loads AppKit (prevents the brief icon flash).
try:
    import ctypes, ctypes.util as _cu
    _objc_lib = ctypes.cdll.LoadLibrary(_cu.find_library('objc'))
    _objc_lib.objc_getClass.restype = ctypes.c_void_p
    _objc_lib.sel_registerName.restype = ctypes.c_void_p
    _objc_lib.objc_msgSend.restype = ctypes.c_void_p
    _objc_lib.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    ctypes.cdll.LoadLibrary(_cu.find_library('AppKit'))
    _cls = _objc_lib.objc_getClass(b'NSApplication')
    _app = _objc_lib.objc_msgSend(_cls, _objc_lib.sel_registerName(b'sharedApplication'))
    _send_long = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long)(('objc_msgSend', _objc_lib))
    _send_long(_app, _objc_lib.sel_registerName(b'setActivationPolicy:'), 1)
    del _objc_lib, _cls, _app, _send_long, _cu
except Exception:
    pass

from Cocoa import (
    NSApplication, NSApplicationActivationPolicyAccessory, NSColor, NSColorSpace, NSFont,
    NSImage, NSImageView, NSMakeRect, NSPanel, NSProgressIndicator,
    NSScreen, NSTextField, NSView, NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary, NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary, NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel, NSWorkspace, NSWindowBelow
)

BG_PAD_X, BG_PAD_Y, BG_ALPHA = 12, 9, 0.46  # overlay background card
NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
from Foundation import NSObject, NSTimer
import Quartz
import objc

SUPPORT = Path.home() / "Library/Application Support/PG3D Mac Client"
STATE_DIR = SUPPORT / "state"
CONFIG = STATE_DIR / "overlay_config.json"
STATE = STATE_DIR / "overlay_runtime.json"
EDIT_REQUEST = STATE_DIR / "overlay_edit_request.json"
RANK_FILE = STATE_DIR / "overlay_rank.json"
FPS_LOG_DIR = SUPPORT / "fps_logs"
MUSIC_CACHE_DIR = SUPPORT / "music_cache"
PIDFILE = SUPPORT / "overlay.pid"
GAME_FPS_HELPER = Path(__file__).resolve().parent / "game_fps.py"
GAME_FPS_JSON = SUPPORT / "state/game_fps.json"

for p in (STATE_DIR, FPS_LOG_DIR, MUSIC_CACHE_DIR):
    p.mkdir(parents=True, exist_ok=True)

# DOM KeyboardEvent.code -> macOS virtual key code.
KEY_CODES = {
    "KeyA":0,"KeyS":1,"KeyD":2,"KeyF":3,"KeyH":4,"KeyG":5,"KeyZ":6,"KeyX":7,"KeyC":8,"KeyV":9,
    "KeyB":11,"KeyQ":12,"KeyW":13,"KeyE":14,"KeyR":15,"KeyY":16,"KeyT":17,
    "Digit1":18,"Digit2":19,"Digit3":20,"Digit4":21,"Digit6":22,"Digit5":23,"Equal":24,"Digit9":25,
    "Digit7":26,"Minus":27,"Digit8":28,"Digit0":29,"BracketRight":30,"KeyO":31,"KeyU":32,"BracketLeft":33,
    "KeyI":34,"KeyP":35,"Enter":36,"KeyL":37,"KeyJ":38,"Quote":39,"KeyK":40,"Semicolon":41,"Backslash":42,
    "Comma":43,"Slash":44,"KeyN":45,"KeyM":46,"Period":47,"Tab":48,"Space":49,"Backquote":50,"Backspace":51,
    "Escape":53,"MetaRight":54,"MetaLeft":55,"ShiftLeft":56,"CapsLock":57,"AltLeft":58,"ControlLeft":59,
    "ShiftRight":60,"AltRight":61,"ControlRight":62,"F17":64,"NumpadDecimal":65,"NumpadMultiply":67,
    "NumpadAdd":69,"NumLock":71,"NumpadDivide":75,"NumpadEnter":76,"NumpadSubtract":78,"F18":79,"F19":80,
    "NumpadEqual":81,"Numpad0":82,"Numpad1":83,"Numpad2":84,"Numpad3":85,"Numpad4":86,"Numpad5":87,
    "Numpad6":88,"Numpad7":89,"F20":90,"Numpad8":91,"Numpad9":92,"F5":96,"F6":97,"F7":98,"F3":99,
    "F8":100,"F9":101,"F11":103,"F13":105,"F16":106,"F14":107,"F10":109,"F12":111,"F15":113,
    "Home":115,"PageUp":116,"Delete":117,"F4":118,"End":119,"F2":120,"PageDown":121,"F1":122,
    "ArrowLeft":123,"ArrowRight":124,"ArrowDown":125,"ArrowUp":126
}
MOD_CODES = {
    "shift": (56, 60),
    "ctrl": (59, 62),
    "alt": (58, 61),
    "cmd": (55, 54),
}
MODIFIER_CODE_NAMES = {
    "ShiftLeft":"shift","ShiftRight":"shift","ControlLeft":"ctrl","ControlRight":"ctrl",
    "AltLeft":"alt","AltRight":"alt","MetaLeft":"cmd","MetaRight":"cmd"
}


def default_hotkey():
    return {"code":"F8","key":"F8","display":"F8","mods":{"shift":False,"ctrl":False,"alt":False,"cmd":False}}


def default_config():
    return {
        "enabled": True,
        "hotkey": default_hotkey(),
        "layout": "free",
        "anchor": "top-right",
        "group": {
            "enabled": True,
            "orientation": "vertical",
            "size_mode": "bundle",
            "size": 18,
            "color": "#FFFFFF",
            "x": 0.5,
            "y": 0.5,
            "placed": False,
        },
        "items": [
            {"id":"fps","enabled":True,"size":18,"color":"#FFFFFF","order":0,"x":0.5,"y":0.5,"placed":False},
            {"id":"clock","enabled":True,"size":18,"color":"#FFFFFF","order":1,"x":0.5,"y":0.5,"placed":False,
             "clock":{"format24":True,"format12":False,"calendar":False,"seconds":False}},
            {"id":"ping","enabled":True,"size":18,"color":"#FFFFFF","order":2,"x":0.5,"y":0.5,"placed":False},
            {"id":"music","enabled":True,"size":16,"color":"#FFFFFF","order":3,"x":0.5,"y":0.5,"placed":False,
             "music":{"lyrics":False,"cover":True,"cover_size":48,"title":True,"artist":True,"album":False,"timestamp":True}},
            {"id":"rank","enabled":False,"size":16,"color":"#FFFFFF","order":4,"x":0.5,"y":0.5,"placed":False},
        ],
    }


def _merge_config(raw):
    base = default_config()
    if not isinstance(raw, dict):
        return base
    base["enabled"] = bool(raw.get("enabled", True))
    hk = raw.get("hotkey")
    if isinstance(hk, str):
        base["hotkey"] = {"code":hk,"key":hk,"display":hk,"mods":{"shift":False,"ctrl":False,"alt":False,"cmd":False}}
    elif isinstance(hk, dict):
        b = default_hotkey(); b.update({k:v for k,v in hk.items() if k in b})
        mods = b.get("mods") if isinstance(b.get("mods"), dict) else {}
        b["mods"] = {m:bool(mods.get(m,False)) for m in ("shift","ctrl","alt","cmd")}
        base["hotkey"] = b
    if isinstance(raw.get("group"), dict):
        base["group"].update(raw["group"])
    base["group"]["enabled"] = bool(base["group"].get("enabled",True))
    base["group"]["orientation"] = "horizontal" if base["group"].get("orientation")=="horizontal" else "vertical"
    base["group"]["size_mode"] = "bundle" if base["group"]["enabled"] else "individual"
    incoming = raw.get("items") if isinstance(raw.get("items"), list) else []
    by_id = {x["id"]:x for x in incoming if isinstance(x,dict) and x.get("id")}
    out=[]
    for template in default_config()["items"]:
        item=json.loads(json.dumps(template))
        src=by_id.get(item["id"],{})
        for k in ("enabled","size","color","order","x","y","placed"):
            if k in src:item[k]=src[k]
        if item["id"]=="music":
            m=dict(template["music"])
            if isinstance(src.get("music"),dict):m.update(src["music"])
            m["album"]=False
            item["music"]=m
        if item["id"]=="clock":
            c=dict(template["clock"])
            if isinstance(src.get("clock"),dict):c.update(src["clock"])
            if bool(c.get("format24"))==bool(c.get("format12")):c["format24"],c["format12"]=True,False
            item["clock"]=c
        if item["id"]=="rank":item["enabled"]=False  # ranks are hidden in the client for now (code kept)
        out.append(item)
    out.sort(key=lambda x:int(x.get("order",0)))
    for i,x in enumerate(out):x["order"]=i
    base["items"]=out
    base["layout"]="free"
    return base


def load_config():
    try:
        return _merge_config(json.loads(CONFIG.read_text()))
    except Exception:
        return default_config()


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True,exist_ok=True)
    tmp=CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg,indent=2))
    os.replace(tmp,CONFIG)

def effective_item(config, item):
    """Return a display copy. Unified color never overwrites saved Individual colors."""
    out = dict(item or {})
    group = config.get("group") if isinstance(config, dict) else {}
    if isinstance(group, dict) and group.get("enabled"):
        out["color"] = str(group.get("color") or "#FFFFFF")
    return out


def hex_color(s, alpha=1.0):
    try:
        h=str(s).lstrip('#')
        if len(h)!=6: raise ValueError
        r,g,b=[int(h[i:i+2],16)/255 for i in (0,2,4)]
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(r,g,b,alpha)
    except Exception:
        return NSColor.colorWithCalibratedRed_green_blue_alpha_(1,1,1,alpha)


def pg3d_frontmost():
    try:
        a=NSWorkspace.sharedWorkspace().frontmostApplication()
        name=str(a.localizedName() or '').lower(); bid=str(a.bundleIdentifier() or '').lower()
        return 'pixel gun 3d' in name or ('pixel' in name and 'gun' in name) or 'pixelgun' in bid
    except Exception:
        return False


FIX_MOUSE_HOTKEY={"code":"KeyM","key":"m","display":"Ctrl+Shift+M","mods":{"shift":True,"ctrl":True,"alt":False,"cmd":False}}
FOCUS_LOG = SUPPORT / "focus.log"
_FOCUS = {"last": None}


def refocus_pg3d():
    """Switch away from PG3D for a moment and back. Unity then re-locks and hides the
    cursor and takes clicks again, the same as Cmd-Tab out and in."""
    try:
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass
    NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.18, False, lambda t: activate_pg3d())
    _focus_note("fix mouse (Ctrl+Shift+M)")


def _focus_note(text):
    try:
        lines = FOCUS_LOG.read_text(encoding="utf-8").splitlines()[-300:] if FOCUS_LOG.exists() else []
        lines.append(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {text}")
        FOCUS_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def log_focus_change():
    """Record which app is in front while PG3D runs, for the diagnostic report."""
    try:
        a = NSWorkspace.sharedWorkspace().frontmostApplication()
        name = str(a.localizedName() or "?")
    except Exception:
        return
    if name != _FOCUS["last"]:
        prev, _FOCUS["last"] = _FOCUS["last"], name
        if prev is not None and pg3d_pid():
            _focus_note(f"front app: {prev} -> {name}")


def activate_pg3d():
    try:
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            name=str(app.localizedName() or '').lower(); bid=str(app.bundleIdentifier() or '').lower()
            if 'pixel gun 3d' in name or 'pixelgun' in bid:
                app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps
                return
    except Exception:
        pass


def pg3d_pid():
    try:
        # the game's own executable only, not helpers that mention its path
        r=subprocess.run(['/bin/ps','-axo','pid=,comm='],capture_output=True,text=True,timeout=2)
        for line in r.stdout.splitlines():
            pid,_,comm=line.strip().partition(' ')
            comm=comm.strip()
            if pid.isdigit() and (comm.endswith('/Contents/MacOS/Pixel Gun 3D') or comm=='Pixel Gun 3D'): return int(pid)
    except Exception: pass
    return None


def fps_value():
    """Frames per second of the running game, from its own measurement lines
    (FPS unlocker or mouse fix). Only lines from the current PG3D process count,
    so an old run's number never shows."""
    pid=pg3d_pid()
    if not pid:return None
    try:
        files=sorted(FPS_LOG_DIR.glob('run-*.log'),key=lambda p:p.stat().st_mtime,reverse=True)[:3]
        for f in files:
            if time.time()-f.stat().st_mtime>30:continue  # measurements arrive every 5 s
            txt=f.read_text(errors='ignore')[-24000:]
            vals=re.findall(r'pid=%d\] engine measurement: rendered=([0-9.]+) FPS'%pid,txt)
            if vals:return round(float(vals[-1]))
    except Exception:pass
    # No in-game measurement (Steam start, or a build the injected helpers can't read):
    # the screen-capture helper counts PG3D's new frames instead.
    try:
        d=json.loads(GAME_FPS_JSON.read_text())
        if time.time()-float(d.get('at',0))<3 and d.get('fps') is not None:return int(d['fps'])
    except Exception:pass
    return None


_FPS_HELPER={'proc':None,'denied_at':0.0}


def ensure_fps_helper(wanted):
    """Run game_fps.py while the overlay shows FPS and PG3D runs."""
    p=_FPS_HELPER['proc']
    if p is not None and p.poll() is not None:
        if p.returncode in (3,4):_FPS_HELPER['denied_at']=time.time()  # no ScreenCaptureKit / no permission: retry later
        _FPS_HELPER['proc']=p=None
    if wanted and p is None and time.time()-_FPS_HELPER['denied_at']>300 and GAME_FPS_HELPER.exists():
        try:
            log_f=open(SUPPORT/'game_fps.log','a')
            _FPS_HELPER['proc']=subprocess.Popen([sys.executable,str(GAME_FPS_HELPER)],stdout=log_f,stderr=log_f,stdin=subprocess.DEVNULL)
            log_f.close()
        except Exception:pass
    elif not wanted and p is not None:
        try:p.terminate()
        except Exception:pass
        _FPS_HELPER['proc']=None


# Ping runs in the background: nettop/ping can take seconds and must never stall
# the overlay (it shares a thread with the window that sits over the game).
_PING={'value':None,'at':0.0,'thread':None}


def _udp_peers(pid):
    """Remote addresses PG3D talks to over UDP (the game server) and TCP."""
    udp,tcp=[],[]
    try:
        r=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(pid),'-i'],capture_output=True,text=True,timeout=3)
        for line in r.stdout.splitlines()[1:]:
            m=re.search(r'(UDP|TCP) \S*->\[?([0-9a-fA-F:.]+?)\]?:(\d+)',line)
            if not m:continue
            ip=m.group(2)
            if ip.startswith(('127.','::1','0.0.0.0')):continue
            (udp if m.group(1)=='UDP' else tcp).append((ip,int(m.group(3))))
    except Exception:pass
    return udp,tcp


def _icmp_ms(ip):
    try:
        r=subprocess.run(['/sbin/ping','-c','1','-W','1000','-t','2',ip],capture_output=True,text=True,timeout=3)
        m=re.search(r'time=([0-9.]+) ms',r.stdout)
        if m:return float(m.group(1))
    except Exception:pass
    return None


def _tcp_ms(ip,port):
    import socket
    try:
        t=time.time(); s=socket.create_connection((ip,port),timeout=1.5); s.close()
        return (time.time()-t)*1000
    except Exception:return None


def _nettop_ms(pid):
    try:
        r=subprocess.run(['/usr/bin/nettop','-n','-P','-L','1','-J','rtt_avg','-p',str(pid)],capture_output=True,text=True,timeout=2.5)
        vals=[]
        for row in list(csv.reader(r.stdout.splitlines()))[1:]:
            for cell in row[1:]:
                m=re.search(r'([0-9]+(?:\.[0-9]+)?)\s*ms',cell,re.I)
                if m:vals.append(float(m.group(1)))
        return min(vals) if vals else None
    except Exception:return None


def _measure_ping():
    pid=pg3d_pid()
    if not pid:return None
    udp,tcp=_udp_peers(pid)
    for ip,_ in udp[:3]:  # the match server: what ping means in a game
        v=_icmp_ms(ip)
        if v is not None:return v
    v=_nettop_ms(pid)
    if v is not None:return v
    for ip,port in tcp[:2]:
        v=_tcp_ms(ip,port)
        if v is not None:return v
    return None


def _ping_worker():
    while True:
        try:
            v=_measure_ping()
            _PING['value']=None if v is None else int(round(v)); _PING['at']=time.time()
        except Exception:pass
        time.sleep(2.0 if _PING['value'] is not None else 4.0)


def ping_value():
    if _PING['thread'] is None:
        _PING['thread']=threading.Thread(target=_ping_worker,daemon=True); _PING['thread'].start()
    return _PING['value'] if time.time()-_PING['at']<10 else None


def _app_running(name):
    try:return subprocess.run(['/usr/bin/pgrep','-x',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
    except Exception:return False


def _osascript(script, timeout=2.0):
    try:
        r=subprocess.run(['/usr/bin/osascript','-e',script],capture_output=True,text=True,timeout=timeout)
        if r.returncode==0:return r.stdout.strip()
    except Exception:pass
    return ''


def music_metadata():
    sep='\x1f'
    if _app_running('Spotify'):
        script='''tell application "Spotify"\nif player state is not playing then return ""\nset t to current track\nset sep to ASCII character 31\nreturn (name of t as text) & sep & (artist of t as text) & sep & (album of t as text) & sep & (duration of t as text) & sep & (player position as text) & sep & (artwork url of t as text)\nend tell'''
        s=_osascript(script)
        if s:
            p=s.split(sep)
            if len(p)>=5:
                try:duration=float(p[3]); duration=duration/1000 if duration>10000 else duration
                except Exception:duration=0
                try:position=float(p[4])
                except Exception:position=0
                return {"provider":"Spotify","title":p[0],"artist":p[1],"album":p[2],"duration":duration,"position":position,"artwork_url":p[5] if len(p)>5 else ''}
    if _app_running('Music'):
        script='''tell application "Music"\nif player state is not playing then return ""\nset t to current track\nset sep to ASCII character 31\nreturn (name of t as text) & sep & (artist of t as text) & sep & (album of t as text) & sep & (duration of t as text) & sep & (player position as text)\nend tell'''
        s=_osascript(script)
        if s:
            p=s.split(sep)
            if len(p)>=5:
                try:duration=float(p[3])
                except Exception:duration=0
                try:position=float(p[4])
                except Exception:position=0
                return {"provider":"Music","title":p[0],"artist":p[1],"album":p[2],"duration":duration,"position":position,"artwork_url":""}
    return None


_LYRICS_CACHE={}
_ART_CACHE={}


def _track_key(meta):
    if not meta:return ''
    return '|'.join(str(meta.get(k,'')).strip().lower() for k in ('title','artist','album'))+f"|{round(float(meta.get('duration') or 0))}"


def _fetch_lyrics(meta):
    key=_track_key(meta)
    if not key:return []
    if key in _LYRICS_CACHE:return _LYRICS_CACHE[key]
    lines=[]
    try:
        params={"track_name":meta.get('title',''),"artist_name":meta.get('artist','')}
        if meta.get('album'):params['album_name']=meta['album']
        if meta.get('duration'):params['duration']=round(float(meta['duration']))
        req=Request('https://lrclib.net/api/get?'+urlencode(params),headers={'User-Agent':'PG3D-Mac-Client/1.16.0'})
        with urlopen(req,timeout=4) as r:data=json.loads(r.read().decode('utf-8'))
        synced=data.get('syncedLyrics') or ''
        for raw in synced.splitlines():
            m=re.match(r'^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$',raw)
            if m:
                lines.append((int(m.group(1))*60+float(m.group(2)),m.group(3).strip()))
    except Exception:
        pass
    _LYRICS_CACHE[key]=lines
    if len(_LYRICS_CACHE)>64:_LYRICS_CACHE.pop(next(iter(_LYRICS_CACHE)))
    return lines


def _current_lyric(meta):
    lines=_fetch_lyrics(meta)
    if not lines:return ''
    pos=float(meta.get('position') or 0); current=''
    for t,text in lines:
        if t<=pos:current=text
        else:break
    return current


def _download_art(meta):
    key=_track_key(meta)
    if not key:return None
    if key in _ART_CACHE:return _ART_CACHE[key]
    url=str(meta.get('artwork_url') or '')
    try:
        if not url:
            term=f"{meta.get('artist','')} {meta.get('title','')}"
            req=Request('https://itunes.apple.com/search?'+urlencode({'term':term,'entity':'song','limit':5}),headers={'User-Agent':'PG3D-Mac-Client/1.16.0'})
            with urlopen(req,timeout=4) as r:data=json.loads(r.read().decode('utf-8'))
            results=data.get('results') or []
            if results:url=results[0].get('artworkUrl100') or ''
            if url:url=url.replace('100x100bb','512x512bb')
        if not url:raise ValueError
        ext='.jpg' if '.jpg' in url.lower() else '.img'
        path=MUSIC_CACHE_DIR/(hashlib.sha256(key.encode()).hexdigest()+ext)
        if not path.exists():
            req=Request(url,headers={'User-Agent':'PG3D-Mac-Client/1.16.0'})
            with urlopen(req,timeout=5) as r:path.write_bytes(r.read())
        _ART_CACHE[key]=str(path)
        return str(path)
    except Exception:
        _ART_CACHE[key]=None
        return None


def enrich_music(meta, cfg):
    if not meta:return None
    out=dict(meta)
    if cfg.get('lyrics'):out['lyric']=_current_lyric(out)
    else:out['lyric']=''
    if cfg.get('cover'):out['art_path']=_download_art(out)
    else:out['art_path']=None
    return out


def key_state(code):
    try:return bool(Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState,int(code)))
    except Exception:return False


def hotkey_pressed(hk):
    if not isinstance(hk,dict):hk=default_hotkey()
    code_name=str(hk.get('code') or 'F8')
    code=KEY_CODES.get(code_name)
    if code is None:return False
    mods=hk.get('mods') if isinstance(hk.get('mods'),dict) else {}
    own_mod=MODIFIER_CODE_NAMES.get(code_name)
    for name,codes in MOD_CODES.items():
        required=bool(mods.get(name,False))
        if own_mod==name:required=False
        if required and not any(key_state(c) for c in codes):return False
    return key_state(code)


def rank_value():
    """Current client rank, written by the server while PG3D runs."""
    try:
        return json.loads(RANK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def rank_text(r):
    if not r or not r.get("name"):
        return "Rank —"
    if r.get("next") is None:
        return f"{r['name']}"
    pct = max(0, min(100, int(round(float(r.get("progress", 0)) * 100))))
    filled = pct // 10
    return f"{r['name']} {'▰' * filled}{'▱' * (10 - filled)} {pct}%"


def clock_text(item, now=None):
    now = now or datetime.now()
    c = item.get("clock") if isinstance(item.get("clock"),dict) else {}
    use12 = bool(c.get("format12",False)) and not bool(c.get("format24",True))
    seconds = bool(c.get("seconds",False))
    if use12:
        fmt = "%I:%M:%S %p" if seconds else "%I:%M %p"
        text = now.strftime(fmt).lstrip("0")
    else:
        fmt = "%H:%M:%S" if seconds else "%H:%M"
        text = now.strftime(fmt)
    if c.get("calendar"):
        text += f"  {now.day}.{now.month}.{str(now.year)[-2:]}"
    return text


class MusicView(NSView):
    def init(self):
        self=objc.super(MusicView,self).initWithFrame_(NSMakeRect(0,0,240,70))
        if self is None:return None
        self.cover=NSImageView.alloc().initWithFrame_(NSMakeRect(0,0,48,48)); self.addSubview_(self.cover)
        self.title=NSTextField.labelWithString_(''); self.artist=NSTextField.labelWithString_(''); self.album=NSTextField.labelWithString_(''); self.lyric=NSTextField.labelWithString_('')
        for v in (self.title,self.artist,self.album,self.lyric):
            v.setSelectable_(False); self.addSubview_(v)
        self.progress=NSView.alloc().initWithFrame_(NSMakeRect(0,0,160,3)); self.progress.setWantsLayer_(True); self.progress.layer().setCornerRadius_(1.5)
        self.progress.layer().setBackgroundColor_(Quartz.CGColorCreateGenericRGB(1,1,1,0.18)); self.addSubview_(self.progress)
        self.fill=NSView.alloc().initWithFrame_(NSMakeRect(0,0,0,3)); self.fill.setWantsLayer_(True); self.fill.layer().setCornerRadius_(1.5); self.progress.addSubview_(self.fill)
        return self

    def update_(self, payload):
        item=payload.get('item') or {}; meta=payload.get('meta') or {}; base=float(payload.get('size') or 16); color=hex_color(item.get('color','#FFFFFF'))
        mc=item.get('music') if isinstance(item.get('music'),dict) else {}
        cover_on=bool(mc.get('cover',True)); cover_size=max(24,min(120,int(mc.get('cover_size',48))))
        title_on=bool(mc.get('title',True)); artist_on=bool(mc.get('artist',True)); album_on=bool(mc.get('album',False)); timestamp_on=bool(mc.get('timestamp',True)); lyrics_on=bool(mc.get('lyrics',False))
        self.cover.setHidden_(not cover_on)
        if cover_on:
            self.cover.setFrame_(NSMakeRect(0,0,cover_size,cover_size)); self.cover.setWantsLayer_(True); self.cover.layer().setCornerRadius_(6.0); self.cover.layer().setMasksToBounds_(True)
            self.cover.setImageScaling_(3)  # proportionally up or down
            art=meta.get('art_path')
            self.cover.setImage_(NSImage.alloc().initWithContentsOfFile_(art) if art and Path(art).exists() else None)
        self.title.setHidden_(not title_on); self.artist.setHidden_(not artist_on); self.album.setHidden_(not album_on); self.progress.setHidden_(not timestamp_on); self.lyric.setHidden_(not lyrics_on)
        self.title.setStringValue_(meta.get('title') or 'Song title'); self.artist.setStringValue_(meta.get('artist') or 'Artist'); self.album.setStringValue_(meta.get('album') or 'Album'); self.lyric.setStringValue_(meta.get('lyric') or 'Live lyrics')
        self.title.setFont_(NSFont.systemFontOfSize_weight_(base,0.5)); self.title.setTextColor_(color)
        sec=hex_color(item.get('color','#FFFFFF'),0.78)
        self.artist.setFont_(NSFont.systemFontOfSize_(max(10,base*0.78))); self.artist.setTextColor_(sec)
        self.album.setFont_(NSFont.systemFontOfSize_(max(9,base*0.7))); self.album.setTextColor_(sec)
        self.lyric.setFont_(NSFont.systemFontOfSize_weight_(max(10,base*0.82),0.35)); self.lyric.setTextColor_(color)
        for v in (self.title,self.artist,self.album,self.lyric):v.sizeToFit()
        text_w=max(150,min(320,max([v.frame().size.width for v in (self.title,self.artist,self.album,self.lyric) if not v.isHidden()] or [150])+6))
        x=cover_size+10 if cover_on else 0; y=0; rows=[]
        for v in (self.title,self.artist,self.album):
            if not v.isHidden():rows.append(v)
        row_h=sum(v.frame().size.height+2 for v in rows)
        progress_h=8 if timestamp_on else 0; lyric_h=self.lyric.frame().size.height+5 if lyrics_on else 0
        h=max(cover_size if cover_on else 0,row_h+progress_h+lyric_h,base+4)
        top=h
        for v in rows:
            fh=v.frame().size.height; top-=fh; v.setFrame_(NSMakeRect(x,top,text_w,fh)); top-=2
        if timestamp_on:
            top-=7; self.progress.setFrame_(NSMakeRect(x,max(0,top),text_w,3)); dur=float(meta.get('duration') or 0); pos=float(meta.get('position') or 0)
            frac=0 if dur<=0 else max(0,min(1,pos/dur)); self.fill.setFrame_(NSMakeRect(0,0,text_w*frac,3))
            c=color.colorUsingColorSpace_(NSColorSpace.sRGBColorSpace()) if hasattr(color,'colorUsingColorSpace_') else None
            self.fill.layer().setBackgroundColor_(Quartz.CGColorCreateGenericRGB(c.redComponent(),c.greenComponent(),c.blueComponent(),0.9) if c else Quartz.CGColorCreateGenericRGB(1,1,1,0.9))
        if lyrics_on:
            lh=self.lyric.frame().size.height; self.lyric.setFrame_(NSMakeRect(x,0,text_w,lh))
        self.setFrameSize_((x+text_w,h))


class DragBox(NSView):
    controller=objc.ivar(); target=objc.ivar(); drag_start=objc.ivar(); origin=objc.ivar()
    def hitTest_(self, point):return self
    def mouseDown_(self,event):
        self.drag_start=event.locationInWindow(); self.origin=self.frame().origin
    def mouseDragged_(self,event):
        p=event.locationInWindow(); dx=p.x-self.drag_start.x; dy=p.y-self.drag_start.y
        b=self.superview().bounds(); f=self.frame(); nx=max(0,min(b.size.width-f.size.width,self.origin.x+dx)); ny=max(0,min(b.size.height-f.size.height,self.origin.y+dy)); self.setFrameOrigin_((nx,ny))


class EditorWindow(NSPanel):
    controller=objc.ivar()
    def canBecomeKeyWindow(self):return True
    def acceptsFirstResponder(self):return True
    def keyDown_(self,event):
        code=int(event.keyCode())
        if self.controller is not None and code==36:  # Return / Enter = save
            self.controller.finish()
        elif self.controller is not None and code==53:  # Escape = cancel
            self.controller.cancel()
        else:
            objc.super(EditorWindow,self).keyDown_(event)


class EditorController(NSObject):
    def initWithOverlay_(self, overlay):
        self=objc.super(EditorController,self).init()
        if self is None:return None
        self.overlay=overlay; self.window=None; self.boxes={}; self.grouped=False
        return self

    def _simple_view(self,item,size):
        item = effective_item(self.overlay.config, item)
        if item['id']=='music':
            v=MusicView.alloc().init(); v.update_({'item':item,'size':size,'meta':{'title':'Song title','artist':'Artist','album':'Album','duration':220,'position':74,'lyric':'Live lyrics'}}); return v
        if item['id']=='rank':text=rank_text(rank_value() or {'name':'Gold','progress':0.42,'next':'Platinum'})
        else:text='144 FPS' if item['id']=='fps' else (clock_text(item) if item['id']=='clock' else '32 ms')
        lab=NSTextField.labelWithString_(text); lab.setTextColor_(hex_color(item.get('color','#FFFFFF'))); lab.setFont_(NSFont.monospacedSystemFontOfSize_weight_(float(size),0.4)); lab.setSelectable_(False); lab.sizeToFit(); return lab

    @objc.python_method  # a plain helper, not an Objective-C selector
    def _wrap(self,target,view,padding=8):
        f=view.frame(); box=DragBox.alloc().initWithFrame_(NSMakeRect(0,0,f.size.width+padding*2,f.size.height+padding*2)); box.controller=self; box.target=target; view.setFrameOrigin_((padding,padding)); box.addSubview_(view); return box

    def open(self):
        if self.window is not None:return
        cfg=self.overlay.config; items=sorted([x for x in cfg.get('items',[]) if x.get('enabled',True)],key=lambda x:x.get('order',0)); group=cfg.get('group') or {}; self.grouped=bool(group.get('enabled'))
        frame=NSScreen.mainScreen().frame(); self.window=EditorWindow.alloc().initWithContentRect_styleMask_backing_defer_(frame,NSWindowStyleMaskBorderless,2,False); self.window.controller=self; self.window.setOpaque_(False); self.window.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0,0.74)); self.window.setLevel_(Quartz.CGWindowLevelForKey(Quartz.kCGMaximumWindowLevelKey)-2); self.window.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces|NSWindowCollectionBehaviorFullScreenAuxiliary|NSWindowCollectionBehaviorStationary)
        cv=self.window.contentView(); w,h=cv.bounds().size.width,cv.bounds().size.height
        info=NSTextField.labelWithString_('Drag to place  ·  Return saves  ·  Esc cancels'); info.setFont_(NSFont.systemFontOfSize_weight_(13,0.45)); info.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(1,0.82)); info.sizeToFit(); info.setFrameOrigin_(((w-info.frame().size.width)/2,h-42)); cv.addSubview_(info)
        size_mode=group.get('size_mode','individual'); bundle_size=int(group.get('size',18))
        if self.grouped:
            inner=[]
            for item in items:
                size=bundle_size if size_mode=='bundle' else int(item.get('size',18)); inner.append((item,self._simple_view(item,size)))
            gap=10; pad=10; orient=group.get('orientation','vertical')
            if orient=='horizontal':
                total_w=sum(v.frame().size.width for _,v in inner)+gap*max(0,len(inner)-1); total_h=max([v.frame().size.height for _,v in inner] or [20]);
                box=DragBox.alloc().initWithFrame_(NSMakeRect(0,0,total_w+pad*2,total_h+pad*2)); x=pad
                for _,v in inner:v.setFrameOrigin_((x,pad+(total_h-v.frame().size.height)/2)); box.addSubview_(v); x+=v.frame().size.width+gap
            else:
                total_w=max([v.frame().size.width for _,v in inner] or [20]); total_h=sum(v.frame().size.height for _,v in inner)+gap*max(0,len(inner)-1); box=DragBox.alloc().initWithFrame_(NSMakeRect(0,0,total_w+pad*2,total_h+pad*2)); y=total_h+pad
                for _,v in inner:y-=v.frame().size.height; v.setFrameOrigin_((pad,y)); box.addSubview_(v); y-=gap
            box.controller=self; box.target='group';
            if group.get('placed'):
                gx=float(group.get('x',.5))*w; gy=float(group.get('y',.5))*h; box.setFrameOrigin_((max(0,min(w-box.frame().size.width,gx)),max(0,min(h-box.frame().size.height,h-gy-box.frame().size.height))))
            else: box.setFrameOrigin_(((w-box.frame().size.width)/2,(h-box.frame().size.height)/2))
            cv.addSubview_(box); self.boxes['group']=box
        else:
            for idx,item in enumerate(items):
                size=bundle_size if size_mode=='bundle' else int(item.get('size',18)); view=self._simple_view(item,size); box=self._wrap(item['id'],view)
                if item.get('placed'):
                    x=float(item.get('x',.5))*w; ytop=float(item.get('y',.5))*h; box.setFrameOrigin_((max(0,min(w-box.frame().size.width,x)),max(0,min(h-box.frame().size.height,h-ytop-box.frame().size.height))))
                else:
                    box.setFrameOrigin_(((w-box.frame().size.width)/2+idx*2,(h-box.frame().size.height)/2+idx*2))
                cv.addSubview_(box); self.boxes[item['id']]=box
        self.window.makeKeyAndOrderFront_(None); self.window.makeFirstResponder_(self.window); NSApplication.sharedApplication().activateIgnoringOtherApps_(True); self.overlay.editor_open=True

    def cancel(self):
        if self.window is None:return
        self.window.orderOut_(None); self.window=None; self.boxes={}; self.overlay.editor_open=False; activate_pg3d()

    def finish(self):
        if self.window is None:return
        cfg=self.overlay.config; cv=self.window.contentView(); w,h=cv.bounds().size.width,cv.bounds().size.height
        if self.grouped and 'group' in self.boxes:
            f=self.boxes['group'].frame(); cfg['group']['x']=max(0,min(.98,f.origin.x/w)); cfg['group']['y']=max(0,min(.98,(h-f.origin.y-f.size.height)/h)); cfg['group']['placed']=True
        else:
            for item in cfg.get('items',[]):
                box=self.boxes.get(item.get('id'))
                if not box:continue
                f=box.frame(); item['x']=max(0,min(.98,f.origin.x/w)); item['y']=max(0,min(.98,(h-f.origin.y-f.size.height)/h)); item['placed']=True
        cfg['layout']='free'; save_config(cfg); self.overlay.config=cfg; self.overlay._rebuild_views(); self.window.orderOut_(None); self.window=None; self.boxes={}; self.overlay.editor_open=False; activate_pg3d()


class Overlay(NSObject):
    def init(self):
        self=objc.super(Overlay,self).init()
        if self is None:return None
        self.config=load_config(); self.views={}; self.visible=False; self.last_key=False; self.last_cfg_mtime=0; self.last_data=0; self.values={}; self.music=None; self.editor_open=False; self.last_edit_mtime=0; self.last_fix=False
        self._build_panel(); self._rebuild_views(); self.editor=EditorController.alloc().initWithOverlay_(self)
        self.timer=NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.05,self,'tick:',None,True)
        return self

    def _build_panel(self):
        scr=NSScreen.mainScreen().frame(); style=NSWindowStyleMaskBorderless|NSWindowStyleMaskNonactivatingPanel
        self.panel=NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(scr,style,2,False); self.panel.setOpaque_(False); self.panel.setBackgroundColor_(NSColor.clearColor()); self.panel.setHasShadow_(False); self.panel.setIgnoresMouseEvents_(True); self.panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces|NSWindowCollectionBehaviorFullScreenAuxiliary|NSWindowCollectionBehaviorStationary|NSWindowCollectionBehaviorIgnoresCycle)
        try:self.panel.setLevel_(Quartz.CGWindowLevelForKey(Quartz.kCGOverlayWindowLevelKey))
        except Exception:self.panel.setLevel_(25)
        try:
            self.panel.setBecomesKeyOnlyIfNeeded_(True); self.panel.setHidesOnDeactivate_(False); self.panel.setCanHide_(False)
        except Exception:pass
        self.panel.orderOut_(None)

    def _item_size(self,item):
        g=self.config.get('group') or {}
        return int(g.get('size',18)) if g.get('size_mode')=='bundle' else int(item.get('size',18))

    def _rebuild_views(self):
        cv=self.panel.contentView()
        for v in self.views.values():v.removeFromSuperview()
        self.views={}
        for item in self.config.get('items',[]):
            if not item.get('enabled',True):continue
            if item['id']=='music':v=MusicView.alloc().init()
            else:
                v=NSTextField.labelWithString_(''); v.setSelectable_(False)
            cv.addSubview_(v); self.views[item['id']]=v
        self._layout()

    def _update_view(self,item):
        v=self.views.get(item['id']); size=self._item_size(item)
        if v is None:return
        display_item = effective_item(self.config, item)
        if item['id']=='music':
            mc=item.get('music') if isinstance(item.get('music'),dict) else {}; meta=enrich_music(self.music,mc) if self.music else None; v.update_({'item':display_item,'size':size,'meta':meta or {}})
        else:
            if item['id']=='clock':
                text=clock_text(item)
            elif item['id']=='rank':
                text=rank_text(self.values.get('rank'))
            else:
                value=self.values.get(item['id']); text=(f'{value} FPS' if item['id']=='fps' and value is not None else ('-- FPS' if item['id']=='fps' else (f'{value} ms' if value is not None else '-- ms')))
            v.setStringValue_(text); v.setTextColor_(hex_color(display_item.get('color','#FFFFFF'))); v.setFont_(NSFont.monospacedSystemFontOfSize_weight_(float(size),0.35)); v.sizeToFit()

    def _layout(self):
        self._place(); self._backgrounds(); self._fit_panel()

    @objc.python_method  # helper, not an Objective-C selector
    def _backgrounds(self):
        """A soft dark box behind the text so it stays readable on bright maps."""
        for b in getattr(self,'bgs',[]):b.removeFromSuperview()
        self.bgs=[]
        if not self.config.get('background',True):return
        cv=self.panel.contentView()
        labels=list(self.views.items())
        if not labels:return
        rects=[]
        if (self.config.get('group') or {}).get('enabled'):
            fr=[v.frame() for _,v in labels]
            x0=min(f.origin.x for f in fr); y0=min(f.origin.y for f in fr)
            x1=max(f.origin.x+f.size.width for f in fr); y1=max(f.origin.y+f.size.height for f in fr)
            rects.append((x0,y0,x1-x0,y1-y0))
        else:
            rects=[(f.origin.x,f.origin.y,f.size.width,f.size.height) for f in (v.frame() for _,v in labels)]
        for x,y,w,h in rects:
            b=NSView.alloc().initWithFrame_(NSMakeRect(x-BG_PAD_X,y-BG_PAD_Y,w+2*BG_PAD_X,h+2*BG_PAD_Y)); b.setWantsLayer_(True)
            b.layer().setBackgroundColor_(Quartz.CGColorCreateGenericRGB(0.04,0.04,0.06,BG_ALPHA)); b.layer().setCornerRadius_(10.0)
            b.layer().setBorderWidth_(1.0); b.layer().setBorderColor_(Quartz.CGColorCreateGenericRGB(1,1,1,0.08))
            cv.addSubview_positioned_relativeTo_(b,NSWindowBelow,None); self.bgs.append(b)

    def _fit_panel(self):
        # The panel only covers the widgets themselves. A screen-sized window from another
        # app over the game made macOS hand focus around: the game lost its mouse lock
        # (system cursor stuck in the middle) and needed a second click to react.
        views=[v for v in list(self.views.values())+list(getattr(self,'bgs',[])) if v.superview() is not None]
        scr=NSScreen.mainScreen().frame()
        if not views:
            return
        pad=2
        fr=[v.frame() for v in views]
        x0=min(f.origin.x for f in fr)-pad; y0=min(f.origin.y for f in fr)-pad
        x1=max(f.origin.x+f.size.width for f in fr)+pad; y1=max(f.origin.y+f.size.height for f in fr)+pad
        for v in views:
            o=v.frame().origin; v.setFrameOrigin_((o.x-x0,o.y-y0))
        new=NSMakeRect(scr.origin.x+x0,scr.origin.y+y0,max(1,x1-x0),max(1,y1-y0))
        cur=self.panel.frame()
        if (round(cur.origin.x),round(cur.origin.y),round(cur.size.width),round(cur.size.height))!=(round(new.origin.x),round(new.origin.y),round(new.size.width),round(new.size.height)):
            self.panel.setFrame_display_(new,True)

    def _place(self):
        scr=NSScreen.mainScreen().frame(); w,h=scr.size.width,scr.size.height; items=sorted([x for x in self.config.get('items',[]) if x.get('enabled',True) and x.get('id') in self.views],key=lambda x:x.get('order',0))
        for it in items:self._update_view(it)
        group=self.config.get('group') or {}
        if group.get('enabled'):
            gap=6 if group.get('orientation','vertical')=='vertical' else 14; orient=group.get('orientation','vertical'); sizes=[self.views[it['id']].frame().size for it in items]
            total_w=(sum(s.width for s in sizes)+gap*max(0,len(sizes)-1)) if orient=='horizontal' else max([s.width for s in sizes] or [0]); total_h=max([s.height for s in sizes] or [0]) if orient=='horizontal' else (sum(s.height for s in sizes)+gap*max(0,len(sizes)-1))
            x=max(0,min(w-total_w,float(group.get('x',.5))*w)); ytop=max(0,min(h-total_h,float(group.get('y',.5))*h));
            if orient=='horizontal':
                cursor=x
                for it in items:
                    v=self.views[it['id']]; f=v.frame(); y=h-ytop-f.size.height; v.setFrameOrigin_((cursor,y)); cursor+=f.size.width+gap
            else:
                cursor=ytop
                for i,it in enumerate(items):
                    if it['id']=='music' and i>0:cursor+=6  # a little air between the stats and the song
                    v=self.views[it['id']]; f=v.frame(); y=h-cursor-f.size.height; v.setFrameOrigin_((x,y)); cursor+=f.size.height+gap
            return
        if self.config.get('layout')=='free':
            for it in items:
                v=self.views[it['id']]; f=v.frame(); x=max(0,min(w-f.size.width,float(it.get('x',.5))*w)); ytop=max(0,min(h-f.size.height,float(it.get('y',.5))*h)); v.setFrameOrigin_((x,h-ytop-f.size.height))
            return
        margin=24; gap=8; anchor=self.config.get('anchor','top-right'); top=anchor.startswith('top'); right=anchor.endswith('right'); cursor=margin
        for it in items:
            v=self.views[it['id']]; f=v.frame(); x=w-margin-f.size.width if right else margin
            if top:y=h-cursor-f.size.height; cursor+=f.size.height+gap
            else:y=cursor; cursor+=f.size.height+gap
            v.setFrameOrigin_((x,y))

    def _reload_config(self):
        try:m=CONFIG.stat().st_mtime
        except Exception:m=0
        if m!=self.last_cfg_mtime:
            self.last_cfg_mtime=m; self.config=load_config(); self._rebuild_views()

    def _check_editor_request(self):
        try:
            if not EDIT_REQUEST.exists():return
            m=EDIT_REQUEST.stat().st_mtime
            if m==self.last_edit_mtime:return
            self.last_edit_mtime=m; EDIT_REQUEST.unlink(missing_ok=True)
            self.editor.open()
        except Exception:pass

    def _update_data(self):
        fps_on=any(x.get('id')=='fps' and x.get('enabled',True) for x in self.config.get('items',[]))
        try:allowed=bool(Quartz.CGPreflightScreenCaptureAccess())
        except Exception:allowed=False
        ensure_fps_helper(bool(self.config.get('enabled',True) and fps_on and allowed and pg3d_pid()))
        self.values={'fps':fps_value(),'ping':ping_value(),'rank':rank_value()}; self.music=music_metadata(); self._layout()

    def tick_(self,timer):
        self._reload_config(); self._check_editor_request(); now=time.time()
        if self.editor_open:
            self.panel.orderOut_(None); return
        front=pg3d_frontmost(); pressed=hotkey_pressed(self.config.get('hotkey'))
        if front and pressed and not self.last_key:self.visible=not self.visible
        self.last_key=pressed
        fix=hotkey_pressed(FIX_MOUSE_HOTKEY)
        if front and fix and not self.last_fix:refocus_pg3d()
        self.last_fix=fix
        log_focus_change()
        should=bool(self.config.get('enabled',True) and front and self.visible)
        # Only touch window ordering when visibility changes; re-ordering every tick fought
        # the game for focus.
        if should and not self.panel.isVisible():self.panel.orderFrontRegardless()
        elif not should and self.panel.isVisible():self.panel.orderOut_(None)
        if now-self.last_data>0.8:
            self.last_data=now; self._update_data()
            try:STATE.write_text(json.dumps({'running':True,'visible':should,'frontmost':front,'editor':False,'at':now}))
            except Exception:pass


def main():
    SUPPORT.mkdir(parents=True,exist_ok=True); CONFIG.parent.mkdir(parents=True,exist_ok=True); PIDFILE.write_text(str(os.getpid()))
    if not CONFIG.exists():save_config(default_config())
    app=NSApplication.sharedApplication(); app.setActivationPolicy_(NSApplicationActivationPolicyAccessory); obj=Overlay.alloc().init(); app.run()

if __name__=='__main__':main()
