#!/usr/bin/env python3
"""MacroClicker server — Flask backend + web UI."""

import json
import os
import sys
import signal
from flask import Flask, jsonify, request

from clicker import MacroEngine
from game_assist import GameAssistManager

app = Flask(__name__)
engine = MacroEngine(support_dir=os.environ.get("MACROCLICKER_SUPPORT"))
assists = GameAssistManager()

# ---------------------------------------------------------------- API — macros

@app.route("/api/health")
def health():
    return jsonify({"app": "macroclicker", "version": "1.0.0"})

@app.route("/api/state")
def get_state():
    s = engine.get_state()
    s["game_assists"] = assists.get_state()
    return jsonify(s)

@app.route("/api/start", methods=["POST"])
def start_all():
    engine.start_all()
    assists.start()
    return jsonify({"ok": True})

@app.route("/api/stop", methods=["POST"])
def stop_all():
    engine.stop_all()
    assists.stop()
    return jsonify({"ok": True})

@app.route("/api/toggle_global", methods=["POST"])
def toggle_global():
    if engine.global_active:
        engine.stop_all()
        assists.stop()
    else:
        engine.start_all()
        assists.start()
    return jsonify({"ok": True, "global_active": engine.global_active})

@app.route("/api/macro", methods=["POST"])
def add_macro():
    data = request.get_json(force=True)
    key = data.get("key", "mouse1")
    interval = int(data.get("interval_ms", 100))
    macro = engine.add_macro(key, interval)
    return jsonify(macro.to_dict())

@app.route("/api/macro/<macro_id>", methods=["DELETE"])
def delete_macro(macro_id):
    engine.remove_macro(macro_id)
    return jsonify({"ok": True})

@app.route("/api/macro/<macro_id>", methods=["PATCH"])
def update_macro(macro_id):
    data = request.get_json(force=True)
    engine.update_macro(
        macro_id,
        key=data.get("key"),
        interval_ms=int(data["interval_ms"]) if "interval_ms" in data else None,
        enabled=data.get("enabled"),
    )
    return jsonify({"ok": True})

@app.route("/api/macro/<macro_id>/toggle", methods=["POST"])
def toggle_macro(macro_id):
    engine.toggle_macro(macro_id)
    return jsonify({"ok": True})

@app.route("/api/toggle_key", methods=["POST"])
def set_toggle_key():
    data = request.get_json(force=True)
    engine.set_toggle_key(data["key"])
    return jsonify({"ok": True})

# ---------------------------------------------------------------- API — game assists

@app.route("/api/assist/bonus", methods=["POST"])
def toggle_bonus():
    data = request.get_json(force=True)
    assists.set_bonus_enabled(data.get("enabled", False))
    return jsonify({"ok": True})

@app.route("/api/assist/chest", methods=["POST"])
def toggle_chest():
    data = request.get_json(force=True)
    assists.set_chest_enabled(data.get("enabled", False))
    return jsonify({"ok": True})

# ---------------------------------------------------------------- UI

@app.route("/")
def index():
    return INDEX_HTML

INDEX_HTML = r"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MacroClicker</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0b0b1a;--surface:#141428;--surface2:#1c1c38;--border:#252550;
  --accent:#7c5cff;--accent-dim:#5a3fd6;
  --green:#00e676;--red:#ff3d5a;--yellow:#ffd740;--orange:#ff9100;
  --text:#e0e0ec;--muted:#70708a;
  --radius:10px;
}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--text);min-height:100vh}

/* Header */
header{background:var(--surface);border-bottom:1px solid var(--border);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
header h1{font-size:20px;font-weight:700;color:var(--accent)}
.hdr-right{display:flex;gap:10px;align-items:center}
.badge{padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.badge.on{background:var(--green);color:#000}
.badge.off{background:var(--red);color:#fff}

/* Buttons */
.btn{padding:8px 16px;border:none;border-radius:var(--radius);font-size:13px;font-weight:600;cursor:pointer;transition:all .15s}
.btn:active{transform:scale(.96)}
.btn-start{background:var(--green);color:#000}
.btn-stop{background:var(--red);color:#fff}
.btn-accent{background:var(--accent);color:#fff}
.btn-accent:hover{background:var(--accent-dim)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.btn-danger{background:transparent;color:var(--red);border:1px solid transparent;padding:6px 10px}
.btn-danger:hover{border-color:var(--red)}
.btn-sm{padding:5px 10px;font-size:12px}

/* Layout */
.container{max-width:640px;margin:0 auto;padding:20px 16px}
.section{margin-bottom:24px}
.section-title{font-size:13px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;display:flex;align-items:center;gap:8px}

/* Toggle key bar */
.toggle-key-bar{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;display:flex;align-items:center;justify-content:space-between}
.toggle-key-bar .info{font-size:13px;color:var(--muted)}
.toggle-key-bar .info strong{color:var(--text)}
.key-chip{font-size:14px;font-weight:700;color:var(--yellow);background:rgba(255,215,64,.1);padding:4px 14px;border-radius:6px;border:1px solid rgba(255,215,64,.25);min-width:50px;text-align:center;cursor:pointer;transition:all .15s}
.key-chip:hover{background:rgba(255,215,64,.2);border-color:var(--yellow)}

/* Add bar */
.add-bar{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.add-bar .key-chip{color:var(--accent);background:rgba(124,92,255,.1);border-color:rgba(124,92,255,.25)}
.add-bar .key-chip:hover{background:rgba(124,92,255,.2)}
.add-bar input[type=number]{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:8px 10px;color:var(--text);font-size:13px;width:90px;outline:none}
.add-bar input:focus{border-color:var(--accent)}
.ms-label{color:var(--muted);font-size:12px}

/* Macro cards */
.macro-list{display:flex;flex-direction:column;gap:8px}
.macro-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:12px 14px;display:flex;align-items:center;gap:12px;transition:all .15s}
.macro-card.active{border-color:var(--green)}
.macro-card.disabled{opacity:.45}

.sw{width:40px;height:22px;border-radius:11px;border:none;background:#333;cursor:pointer;position:relative;transition:background .2s;flex-shrink:0}
.sw.on{background:var(--green)}
.sw::after{content:'';position:absolute;width:18px;height:18px;background:#fff;border-radius:9px;top:2px;left:2px;transition:left .2s}
.sw.on::after{left:20px}

.macro-body{flex:1;min-width:0;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.macro-key{font-weight:700;font-size:14px;color:var(--accent);cursor:pointer;padding:2px 10px;border:1px dashed var(--border);border-radius:6px;transition:all .15s}
.macro-key:hover{border-color:var(--accent);background:rgba(124,92,255,.08)}
.macro-interval-wrap{display:flex;align-items:center;gap:4px}
.macro-interval-wrap input{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:4px 8px;color:var(--text);font-size:13px;width:70px;outline:none;text-align:right}
.macro-interval-wrap input:focus{border-color:var(--accent)}
.macro-interval-wrap .ms-label{font-size:11px}

.status-dot{width:8px;height:8px;border-radius:4px;flex-shrink:0}
.status-dot.running{background:var(--green);box-shadow:0 0 6px var(--green)}
.status-dot.stopped{background:var(--muted)}

.empty{text-align:center;padding:30px;color:var(--muted);font-size:14px}

/* Game assists */
.assist-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;margin-bottom:8px;display:flex;align-items:center;justify-content:space-between}
.assist-info h3{font-size:14px;color:var(--orange);margin-bottom:2px}
.assist-info p{font-size:12px;color:var(--muted)}
.assist-stat{font-size:11px;color:var(--muted);margin-top:4px}
.assist-right{display:flex;align-items:center;gap:10px}

/* Key capture overlay */
.overlay{position:fixed;inset:0;background:rgba(0,0,0,.75);display:flex;align-items:center;justify-content:center;z-index:100}
.overlay-box{background:var(--surface);border:2px solid var(--accent);border-radius:16px;padding:30px 40px;text-align:center;min-width:280px}
.overlay-box h3{color:var(--accent);margin-bottom:8px;font-size:16px}
.overlay-box p{color:var(--text);font-size:14px;min-height:22px}
.overlay-box .hint{margin-top:14px;font-size:11px;color:var(--muted)}
</style>
</head>
<body>

<header>
  <h1>MacroClicker</h1>
  <div class="hdr-right">
    <span class="badge off" id="g-badge">OFF</span>
    <button class="btn btn-start btn-sm" onclick="startAll()">Start All</button>
    <button class="btn btn-stop btn-sm" onclick="stopAll()">Stop All</button>
  </div>
</header>

<div class="container">

  <!-- Global toggle key -->
  <div class="section">
    <div class="toggle-key-bar">
      <div class="info">Global toggle key <strong>(start/stop all)</strong></div>
      <div class="key-chip" id="toggle-key" onclick="capture('toggle')">F6</div>
    </div>
  </div>

  <!-- Add macro -->
  <div class="section">
    <div class="section-title">Macros</div>
    <div class="add-bar">
      <div class="key-chip" id="add-key-chip" onclick="capture('add')">mouse1</div>
      <input type="number" id="add-ms" value="100" min="10" step="10">
      <span class="ms-label">ms</span>
      <button class="btn btn-accent btn-sm" onclick="addMacro()">+ Add Macro</button>
    </div>
  </div>

  <!-- Macro list -->
  <div class="section">
    <div class="macro-list" id="macro-list">
      <div class="empty" id="empty">No macros yet — add one above.</div>
    </div>
  </div>

  <!-- Game Assists -->
  <div class="section">
    <div class="section-title">Idle Slayer Assists</div>

    <div class="assist-card">
      <div class="assist-info">
        <h3>Bonus Stage Runner</h3>
        <p>Auto-detects and clears bonus stages</p>
        <div class="assist-stat" id="bonus-stat">Stages cleared: 0</div>
      </div>
      <div class="assist-right">
        <button class="sw" id="sw-bonus" onclick="toggleAssist('bonus')"></button>
      </div>
    </div>

    <div class="assist-card">
      <div class="assist-info">
        <h3>Chest Hunt Solver</h3>
        <p>Solves the 3x3 chest minigame automatically</p>
        <div class="assist-stat" id="chest-stat">Hunts solved: 0</div>
      </div>
      <div class="assist-right">
        <button class="sw" id="sw-chest" onclick="toggleAssist('chest')"></button>
      </div>
    </div>
  </div>

</div>

<!-- Key capture overlay -->
<div class="overlay" id="overlay" style="display:none">
  <div class="overlay-box">
    <h3 id="overlay-title">Press any key or click a mouse button</h3>
    <p id="overlay-val">Waiting...</p>
    <div class="hint">Escape to cancel</div>
  </div>
</div>

<script>
let S = {global_active:false, toggle_key:'f6', macros:[], game_assists:{bonus_stage:{},chest_hunt:{}}};
let captureTarget = null; // 'toggle' | 'add' | macro id
let addKey = 'mouse1';
let bonusOn = false, chestOn = false;

const KMAP = {' ':'space','ArrowUp':'up','ArrowDown':'down','ArrowLeft':'left','ArrowRight':'right','Enter':'return','Backspace':'delete'};
function kname(e){if(e.key==='Escape')return'escape';if(KMAP[e.key])return KMAP[e.key];if(e.key.length===1)return e.key.toLowerCase();return e.key.toLowerCase()}
function mbtn(b){return'mouse'+(b+1)}

async function api(p,m='GET',b=null){
  const o={method:m};
  if(b){o.headers={'Content-Type':'application/json'};o.body=JSON.stringify(b)}
  return(await fetch('/api'+p,o)).json();
}

async function refresh(){
  S=await api('/state');
  render();
}

function render(){
  // badge
  const bd=document.getElementById('g-badge');
  bd.textContent=S.global_active?'ON':'OFF';
  bd.className='badge '+(S.global_active?'on':'off');

  // toggle key
  document.getElementById('toggle-key').textContent=S.toggle_key.toUpperCase();

  // macros
  const list=document.getElementById('macro-list');
  const empty=document.getElementById('empty');
  if(!S.macros.length){list.innerHTML='';list.appendChild(empty);empty.style.display='';return}
  empty.style.display='none';
  list.innerHTML='';

  for(const m of S.macros){
    const c=document.createElement('div');
    c.className='macro-card'+(m.running?' active':'')+(!m.enabled?' disabled':'');
    c.innerHTML=`
      <button class="sw${m.enabled?' on':''}" onclick="toggleM('${m.id}')"></button>
      <div class="status-dot ${m.running?'running':'stopped'}"></div>
      <div class="macro-body">
        <div class="macro-key" onclick="capture('${m.id}')" title="Click to change key">${m.key.toUpperCase()}</div>
        <div class="macro-interval-wrap">
          <input type="number" value="${m.interval_ms}" min="10" step="10"
                 onchange="updateMs('${m.id}',this.value)">
          <span class="ms-label">ms</span>
        </div>
      </div>
      <button class="btn-danger btn-sm" onclick="delM('${m.id}')">✕</button>
    `;
    list.appendChild(c);
  }

  // assists
  const ga=S.game_assists||{};
  const bs=ga.bonus_stage||{};
  const ch=ga.chest_hunt||{};
  bonusOn=!!bs.enabled; chestOn=!!ch.enabled;
  document.getElementById('sw-bonus').className='sw'+(bonusOn?' on':'');
  document.getElementById('sw-chest').className='sw'+(chestOn?' on':'');
  document.getElementById('bonus-stat').textContent='Stages cleared: '+(bs.stages_cleared||0);
  document.getElementById('chest-stat').textContent='Hunts solved: '+(ch.hunts_solved||0);
}

async function startAll(){await api('/start','POST');refresh()}
async function stopAll(){await api('/stop','POST');refresh()}
async function addMacro(){
  const ms=parseInt(document.getElementById('add-ms').value)||100;
  await api('/macro','POST',{key:addKey,interval_ms:ms});
  refresh();
}
async function delM(id){await api('/macro/'+id,'DELETE');refresh()}
async function toggleM(id){await api('/macro/'+id+'/toggle','POST');refresh()}
async function updateMs(id,v){await api('/macro/'+id,'PATCH',{interval_ms:parseInt(v)||100});refresh()}

async function toggleAssist(which){
  if(which==='bonus'){bonusOn=!bonusOn;await api('/assist/bonus','POST',{enabled:bonusOn})}
  else{chestOn=!chestOn;await api('/assist/chest','POST',{enabled:chestOn})}
  refresh();
}

/* Key capture */
function capture(target){
  captureTarget=target;
  document.getElementById('overlay').style.display='flex';
  document.getElementById('overlay-val').textContent='Waiting...';
  document.getElementById('overlay-title').textContent=
    target==='toggle'?'Press the new global toggle key':
    target==='add'?'Press a key or click a mouse button':
    'Press the new key for this macro';
}
function finishCapture(name){
  document.getElementById('overlay').style.display='none';
  if(!captureTarget)return;
  if(captureTarget==='toggle'){api('/toggle_key','POST',{key:name}).then(refresh)}
  else if(captureTarget==='add'){addKey=name;document.getElementById('add-key-chip').textContent=name.toUpperCase()}
  else{api('/macro/'+captureTarget,'PATCH',{key:name}).then(refresh)}
  captureTarget=null;
}

document.addEventListener('keydown',e=>{
  if(!captureTarget)return;
  e.preventDefault();
  if(e.key==='Escape'){document.getElementById('overlay').style.display='none';captureTarget=null;return}
  const n=kname(e);
  document.getElementById('overlay-val').textContent=n.toUpperCase();
  setTimeout(()=>finishCapture(n),150);
});
document.getElementById('overlay').addEventListener('mousedown',e=>{
  if(!captureTarget)return;
  e.preventDefault();
  const n=mbtn(e.button);
  document.getElementById('overlay-val').textContent=n.toUpperCase();
  setTimeout(()=>finishCapture(n),150);
});
document.getElementById('overlay').addEventListener('contextmenu',e=>e.preventDefault());

refresh();
setInterval(refresh,1000);
</script>
</body>
</html>"""

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 39100
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
