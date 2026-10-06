"""PG3D WebSocket client for integration into the macOS client.

Headless, thread-safe, event-buffered.  No interactive CLI.
Ported from https://github.com/0xdsyx/pg3d-websocket-client
"""
from __future__ import annotations

import json
import ssl
import threading
import time
from collections import deque
from random import randint

import websocket

from . import config as cfg
from .crypto import AesFacade, KeyDerivation, PacketCodec


class PG3DWebSocket:
    MAX_EVENTS = 200

    def __init__(self):
        self._lock = threading.Lock()
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._sid: str | None = None
        self._aes: AesFacade | None = None
        self._codec: PacketCodec | None = None
        self._token: str = ""
        self._creds: dict = {}
        self._ws_base_url: str = ""
        self._account: dict = {}
        self._ready = False
        self._connecting = False
        self._error: str = ""
        self._expected = 0
        self._buffer: list = []
        self._events: deque = deque(maxlen=self.MAX_EVENTS)
        self._event_id = 0

    @property
    def state(self) -> str:
        if self._ready:
            return "connected"
        if self._connecting:
            return "connecting"
        return "disconnected"

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self.state,
                "account": dict(self._account) if self._account else None,
                "error": self._error,
                "creds": {
                    "uniq_id": self._creds.get("uniq_id", ""),
                } if self._creds else None,
            }

    def events(self, after: int = 0) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["id"] > after]

    def connect(self, creds: dict, token: str, ws_base_url: str):
        with self._lock:
            if self._connecting or self._ready:
                raise RuntimeError("Already connected or connecting")
            self._creds = creds
            self._token = token
            self._ws_base_url = ws_base_url
            if not self._ws_base_url.endswith(cfg.SOCKET_IO_PATH):
                self._ws_base_url = self._ws_base_url.rstrip("/") + cfg.SOCKET_IO_PATH
            self._connecting = True
            self._error = ""
            self._account = {}
            self._ready = False
            self._events.clear()
            self._event_id = 0

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def disconnect(self):
        ws = None
        with self._lock:
            ws = self._ws
            self._ready = False
            self._connecting = False
        if ws:
            try:
                ws.close()
            except Exception:
                pass

    def emit(self, event: str, payload: dict):
        with self._lock:
            if not self._ready or self._codec is None:
                raise RuntimeError("Not connected")
            if isinstance(payload, dict):
                payload.setdefault("sid", self._sid)
                payload.setdefault("req_id", randint(1000, 9999))
            frames = self._codec.encode([event, payload])

        head = frames[0].decode("utf-8") if isinstance(frames[0], bytes) else frames[0]
        self._ws.send(head, websocket.ABNF.OPCODE_TEXT)
        for frame in frames[1:]:
            self._ws.send(frame, websocket.ABNF.OPCODE_BINARY)

        self._push_event(">>", event, payload)

    def _push_event(self, direction: str, event: str, data):
        with self._lock:
            self._event_id += 1
            self._events.append({
                "id": self._event_id,
                "dir": direction,
                "event": str(event),
                "data": data if isinstance(data, (dict, list, str)) else str(data),
                "ts": time.time(),
            })

    def _run(self):
        url = self._ws_base_url + (
            f"?EIO=4&transport=websocket"
            f"&client_version={cfg.GAME_VERSION}&c=1&client_ver=v2&platform=1"
        )
        self._ws = websocket.WebSocketApp(
            url,
            header=cfg.WS_HEADERS,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})

    def _on_open(self, ws):
        self._push_event("<<", "ws_open", "connected to server")

    def _on_error(self, ws, error):
        with self._lock:
            self._error = str(error)
        self._push_event("<<", "ws_error", str(error))

    def _on_close(self, ws, code, msg):
        with self._lock:
            self._ready = False
            self._connecting = False
        self._push_event("<<", "ws_close", f"code={code} msg={msg}")

    def _on_message(self, ws, raw):
        msg = raw if isinstance(raw, bytes) else raw.encode("utf-8")

        if msg.startswith(b"2"):
            ws.send(b"3" + msg[1:], websocket.ABNF.OPCODE_TEXT)
            return

        if msg == b"3":
            return

        if msg.startswith(b'0{"sid"'):
            data = json.loads(msg[1:])
            with self._lock:
                self._sid = data["sid"]
                self._aes = AesFacade(KeyDerivation.master_key(self._sid))
                self._codec = PacketCodec(self._aes)
            threading.Thread(target=self._heartbeat, daemon=True).start()
            self._push_event("<<", "engine_open", {"sid": self._sid})
            return

        if msg == b"40":
            ws.send("40/sio,", websocket.ABNF.OPCODE_TEXT)
            return

        if msg.startswith(b"40/sio"):
            self._send_auth_v2()
            return

        if msg.startswith(b"45"):
            try:
                self._expected = int(msg.split(b"-", 1)[0][2:])
                self._buffer = [msg]
            except ValueError:
                self._expected = 0
                self._buffer = []
            return

        if self._expected > 0:
            self._buffer.append(msg)
            if len(self._buffer) == self._expected + 1:
                frames = self._buffer
                self._buffer = []
                self._expected = 0
                self._process_event(frames)

    def _send_auth_v2(self):
        payload = {
            "uniq_id": self._creds["uniq_id"],
            "hash": self._creds.get("hash", ""),
            "device": self._creds.get("device_id", ""),
            "device_id": self._creds.get("device_id", ""),
            "token": self._token,
            "return": KeyDerivation.return_value(self._token),
            "platform": "1",
            "device_model": cfg.DEFAULT_DEVICE_MODEL,
            "type_device": 2,
            "abuse_method": 1,
            "block_param": 1,
            "time_zone": cfg.DEFAULT_TIME_ZONE,
            "get_reg_ver": 1,
            "req_id": 1,
            "sid": self._sid,
        }
        with self._lock:
            frames = self._codec.encode(["auth_v2", payload])

        head = frames[0].decode("utf-8") if isinstance(frames[0], bytes) else frames[0]
        self._ws.send(head, websocket.ABNF.OPCODE_TEXT)
        for frame in frames[1:]:
            self._ws.send(frame, websocket.ABNF.OPCODE_BINARY)
        self._push_event(">>", "auth_v2", {"uniq_id": self._creds["uniq_id"]})

    def _process_event(self, frames: list):
        with self._lock:
            codec = self._codec
        if codec is None:
            return
        try:
            packet = codec.decode(frames)
        except Exception:
            return
        if not packet:
            return

        event = str(packet[0]) if packet else "unknown"
        body = packet[1] if len(packet) > 1 else {}

        if event == "expired_session":
            with self._lock:
                self._ready = False
                self._error = "Session expired"
            try:
                self._ws.close()
            except Exception:
                pass
            self._push_event("<<", event, body)
            return

        if event == "auth_v2" and isinstance(body, dict) and body.get("status") == "ok":
            with self._lock:
                self._account = body
                self._ready = True
                self._connecting = False
            self._push_event("<<", event, body)
            return

        self._push_event("<<", event, body)

    def _heartbeat(self):
        while self._ws and self._ready or self._connecting:
            time.sleep(14)
            try:
                if self._ws:
                    self._ws.send("2", websocket.ABNF.OPCODE_TEXT)
            except Exception:
                break
