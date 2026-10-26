#!/usr/bin/env python3
"""MacroClicker server — Flask backend + web UI."""

import json
import os
import sys
import signal
from flask import Flask, jsonify, request

from clicker import MacroEngine

app = Flask(__name__)
engine = MacroEngine(support_dir=os.environ.get("MACROCLICKER_SUPPORT"))

# ---------------------------------------------------------------- API

@app.route("/api/health")
def health():
    return jsonify({"app": "macroclicker", "version": "1.0.0"})


@app.route("/api/state")
def get_state():
    return jsonify(engine.get_state())


@app.route("/api/start", methods=["POST"])
def start_all():
    engine.start_all()
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def stop_all():
    engine.stop_all()
    return jsonify({"ok": True})


@app.route("/api/toggle_global", methods=["POST"])
def toggle_global():
    if engine.global_active:
        engine.stop_all()
    else:
        engine.start_all()
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
  --bg:#0b0b1a;--surface:#141428;--border:#252550;
  --accent:#7c5cff;--accent-dim:#5a3fd6;
  --green:#00e676;--red:#ff3d5a;--yellow:#ffd740;
  --text:#e0e0ec;--muted:#70708a;
  --radius:10px;
}
body{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:var(--bg);color:var(--text);
  min-height:100vh;
}
header{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:18px 24px;display:flex;align-items:center;justify-content:space-between;
}
header h1{font-size:20px;font-weight:700;color:var(--accent)}
.global-controls{display:flex;gap:10px;align-items:center}
.badge{
  padding:4px 12px;border-radius:20px;font-size:12px;font-weight:700;
  text-transform:uppercase;letter-spacing:.5px;
}
.badge.on{background:var(--green);color:#000}
.badge.off{background:var(--red);color:#fff}
.btn{
  padding:8px 18px;border:none;border-radius:var(--radius);
  font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;
}
.btn:active{transform:scale(.96)}
.btn-start{background:var(--green);color:#000}
.btn-stop{background:var(--red);color:#fff}
.btn-accent{background:var(--accent);color:#fff}
.btn-accent:hover{background:var(--accent-dim)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text)}
.btn-ghost:hover{border-color:var(--accent);color:var(--accent)}
.btn-danger{background:transparent;border:1px solid var(--red);color:var(--red)}
.btn-danger:hover{background:var(--red);color:#fff}

.container{max-width:640px;margin:0 auto;padding:20px 16px}

.toggle-key-section{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:16px;margin-bottom:20px;
  display:flex;align-items:center;justify-content:space-between;
}
.toggle-key-section .label{font-size:13px;color:var(--muted)}
.toggle-key-section .key-display{
  font-size:15px;font-weight:700;color:var(--yellow);
  background:rgba(255,215,64,.1);padding:4px 14px;border-radius:6px;
  border:1px solid rgba(255,215,64,.25);min-width:60px;text-align:center;
}

.add-bar{
  display:flex;gap:8px;margin-bottom:20px;flex-wrap:wrap;
}
.add-bar input,.add-bar select{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:8px 12px;color:var(--text);
  font-size:13px;outline:none;
}
.add-bar input:focus,.add-bar select:focus{border-color:var(--accent)}
.add-bar input[type=number]{width:100px}

.macro-list{display:flex;flex-direction:column;gap:10px}
.macro-card{
  background:var(--surface);border:1px solid var(--border);
  border-radius:var(--radius);padding:14px 16px;
  display:flex;align-items:center;gap:14px;
  transition:border-color .15s;
}
.macro-card.active{border-color:var(--green)}
.macro-card.disabled{opacity:.5}

.macro-toggle{
  width:44px;height:24px;border-radius:12px;border:none;
  background:#333;cursor:pointer;position:relative;transition:background .2s;
  flex-shrink:0;
}
.macro-toggle.on{background:var(--green)}
.macro-toggle::after{
  content:'';position:absolute;width:20px;height:20px;
  background:#fff;border-radius:10px;top:2px;left:2px;
  transition:left .2s;
}
.macro-toggle.on::after{left:22px}

.macro-info{flex:1;min-width:0}
.macro-key{font-weight:700;font-size:15px;color:var(--accent)}
.macro-interval{font-size:12px;color:var(--muted);margin-top:2px}
.macro-status{
  font-size:11px;font-weight:600;
  padding:2px 8px;border-radius:10px;margin-top:4px;display:inline-block;
}
.macro-status.running{background:rgba(0,230,118,.15);color:var(--green)}
.macro-status.stopped{background:rgba(112,112,138,.15);color:var(--muted)}

.macro-actions{display:flex;gap:6px;flex-shrink:0}

.edit-row{
  display:flex;gap:8px;margin-top:10px;align-items:center;flex-wrap:wrap;
}
.edit-row input{
  background:var(--bg);border:1px solid var(--border);border-radius:6px;
  padding:6px 10px;color:var(--text);font-size:12px;width:90px;outline:none;
}
.edit-row input:focus{border-color:var(--accent)}

.empty{text-align:center;padding:40px;color:var(--muted);font-size:14px}

.key-capture{
  position:fixed;inset:0;background:rgba(0,0,0,.7);
  display:flex;align-items:center;justify-content:center;z-index:100;
}
.key-capture-box{
  background:var(--surface);border:2px solid var(--accent);
  border-radius:16px;padding:30px 40px;text-align:center;
}
.key-capture-box h3{color:var(--accent);margin-bottom:8px}
.key-capture-box p{color:var(--muted);font-size:13px}
.key-capture-box .hint{margin-top:12px;font-size:11px;color:var(--muted)}
</style>
</head>
<body>
<header>
  <h1>MacroClicker</h1>
  <div class="global-controls">
    <span class="badge off" id="global-badge">OFF</span>
    <button class="btn btn-start" id="btn-start" onclick="startAll()">Start All</button>
    <button class="btn btn-stop" id="btn-stop" onclick="stopAll()">Stop All</button>
  </div>
</header>

<div class="container">
  <!-- Global toggle key -->
  <div class="toggle-key-section">
    <div>
      <div class="label">Global Start/Stop Key</div>
      <div style="font-size:12px;color:var(--muted);margin-top:2px">
        Press this key to toggle all macros on/off
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px">
      <div class="key-display" id="toggle-key-display">F6</div>
      <button class="btn btn-ghost" onclick="captureKey('toggle')">Change</button>
    </div>
  </div>

  <!-- Add macro bar -->
  <div class="add-bar">
    <button class="btn btn-ghost" id="key-pick" onclick="captureKey('add')">
      Key: mouse1
    </button>
    <input type="number" id="add-interval" value="100" min="10" step="10" placeholder="ms">
    <span style="color:var(--muted);font-size:13px;align-self:center">ms</span>
    <button class="btn btn-accent" onclick="addMacro()">+ Add Macro</button>
  </div>

  <!-- Macro list -->
  <div class="macro-list" id="macro-list">
    <div class="empty" id="empty-msg">No macros yet. Add one above.</div>
  </div>
</div>

<!-- Key capture overlay -->
<div class="key-capture" id="capture-overlay" style="display:none">
  <div class="key-capture-box">
    <h3>Press any key or mouse button</h3>
    <p id="capture-label">Waiting for input...</p>
    <div class="hint">Press Escape to cancel</div>
  </div>
</div>

<script>
let state = { global_active: false, toggle_key: 'f6', macros: [] };
let pendingCapture = null; // 'toggle' | 'add' | macro_id
let addKey = 'mouse1';

const KEY_NAMES = {
  ' ': 'space', 'ArrowUp': 'up', 'ArrowDown': 'down',
  'ArrowLeft': 'left', 'ArrowRight': 'right',
  'Enter': 'return', 'Backspace': 'delete',
};

function keyName(e) {
  if (e.key === 'Escape') return 'escape';
  if (KEY_NAMES[e.key]) return KEY_NAMES[e.key];
  if (e.key.length === 1) return e.key.toLowerCase();
  return e.key.toLowerCase().replace('arrow', '');
}

function mouseButtonName(btn) {
  return 'mouse' + (btn + 1);
}

async function api(path, method = 'GET', body = null) {
  const opts = { method };
  if (body) { opts.headers = { 'Content-Type': 'application/json' }; opts.body = JSON.stringify(body); }
  const r = await fetch('/api' + path, opts);
  return r.json();
}

async function refresh() {
  state = await api('/state');
  render();
}

function render() {
  const badge = document.getElementById('global-badge');
  badge.textContent = state.global_active ? 'ON' : 'OFF';
  badge.className = 'badge ' + (state.global_active ? 'on' : 'off');

  document.getElementById('toggle-key-display').textContent = state.toggle_key.toUpperCase();

  const list = document.getElementById('macro-list');
  const empty = document.getElementById('empty-msg');

  if (state.macros.length === 0) {
    list.innerHTML = '';
    list.appendChild(empty);
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  list.innerHTML = '';

  for (const m of state.macros) {
    const card = document.createElement('div');
    card.className = 'macro-card' + (m.running ? ' active' : '') + (!m.enabled ? ' disabled' : '');

    card.innerHTML = `
      <button class="macro-toggle${m.enabled ? ' on' : ''}"
              onclick="toggleMacro('${m.id}')"></button>
      <div class="macro-info">
        <div class="macro-key">${m.key.toUpperCase()}</div>
        <div class="macro-interval">Every ${m.interval_ms} ms</div>
        <span class="macro-status ${m.running ? 'running' : 'stopped'}">
          ${m.running ? 'Running' : 'Stopped'}
        </span>
      </div>
      <div class="macro-actions">
        <button class="btn btn-ghost" onclick="editMacro('${m.id}')">Edit</button>
        <button class="btn btn-danger" onclick="deleteMacro('${m.id}')">Del</button>
      </div>
    `;
    list.appendChild(card);
  }
}

async function startAll() { await api('/start', 'POST'); refresh(); }
async function stopAll() { await api('/stop', 'POST'); refresh(); }

async function addMacro() {
  const ms = parseInt(document.getElementById('add-interval').value) || 100;
  await api('/macro', 'POST', { key: addKey, interval_ms: ms });
  refresh();
}

async function deleteMacro(id) { await api('/macro/' + id, 'DELETE'); refresh(); }
async function toggleMacro(id) { await api('/macro/' + id + '/toggle', 'POST'); refresh(); }

async function editMacro(id) {
  const m = state.macros.find(x => x.id === id);
  if (!m) return;
  const ms = prompt('Interval (ms):', m.interval_ms);
  if (ms === null) return;
  await api('/macro/' + id, 'PATCH', { interval_ms: parseInt(ms) || m.interval_ms });
  refresh();
}

function captureKey(target) {
  pendingCapture = target;
  document.getElementById('capture-overlay').style.display = 'flex';
  document.getElementById('capture-label').textContent = 'Waiting for input...';
}

function finishCapture(name) {
  document.getElementById('capture-overlay').style.display = 'none';
  if (pendingCapture === 'toggle') {
    api('/toggle_key', 'POST', { key: name }).then(refresh);
  } else if (pendingCapture === 'add') {
    addKey = name;
    document.getElementById('key-pick').textContent = 'Key: ' + name.toUpperCase();
  } else if (pendingCapture) {
    api('/macro/' + pendingCapture, 'PATCH', { key: name }).then(refresh);
  }
  pendingCapture = null;
}

document.addEventListener('keydown', (e) => {
  if (pendingCapture) {
    e.preventDefault();
    if (e.key === 'Escape') {
      document.getElementById('capture-overlay').style.display = 'none';
      pendingCapture = null;
      return;
    }
    const name = keyName(e);
    document.getElementById('capture-label').textContent = name.toUpperCase();
    setTimeout(() => finishCapture(name), 200);
  }
});

document.getElementById('capture-overlay').addEventListener('mousedown', (e) => {
  if (pendingCapture) {
    e.preventDefault();
    const name = mouseButtonName(e.button);
    document.getElementById('capture-label').textContent = name.toUpperCase();
    setTimeout(() => finishCapture(name), 200);
  }
});
document.getElementById('capture-overlay').addEventListener('contextmenu', (e) => e.preventDefault());

refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 39100
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
