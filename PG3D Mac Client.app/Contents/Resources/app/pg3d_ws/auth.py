"""HTTP authentication and login-code handling for PG3D servers."""
from __future__ import annotations

import base64
import ssl
import time
from random import randint
from urllib.request import Request, urlopen

from . import config as cfg
from .crypto import HttpsCodec


def decode_login_code(code: str) -> dict | None:
    try:
        text = code.strip().strip("\"'")
        if not text:
            return None
        pad = len(text) % 4
        if pad:
            text += "=" * (4 - pad)
        raw = base64.b64decode(text).decode("utf-8")
        parts = raw.split(":")
        if len(parts) != 3:
            return None
        uniq_id, account_hash, device_id = [p.strip() for p in parts]
        if not uniq_id or not account_hash or not device_id:
            return None
        return {"uniq_id": uniq_id, "hash": account_hash, "device_id": device_id}
    except Exception:
        return None


def encode_login_code(creds: dict) -> str:
    raw = f"{creds['uniq_id']}:{creds['hash']}:{creds['device_id']}"
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def authenticate(creds: dict, max_retries: int = 10) -> dict | None:
    codec = HttpsCodec()
    payload = {
        "uniq_id": creds["uniq_id"],
        "hash": creds["hash"],
        "device_id": creds["device_id"],
        "version": cfg.GAME_VERSION,
        "platform": cfg.DEFAULT_PLATFORM,
        "cpu_model": cfg.DEFAULT_CPU_MODEL,
        "device_model": cfg.DEFAULT_DEVICE_MODEL,
        "ram": cfg.DEFAULT_RAM,
        "cpu_cores": cfg.DEFAULT_CPU_CORES,
        "cpu_frequency": cfg.DEFAULT_CPU_FREQUENCY,
        "id_seed": str(randint(100_000_000, 999_999_999)),
    }

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for attempt in range(max_retries):
        try:
            body = codec.encode(payload)
            req = Request(cfg.AUTH_URL, data=body, method="POST")
            for k, v in cfg.HTTP_HEADERS.items():
                req.add_header(k, v)
            with urlopen(req, context=ctx, timeout=8) as resp:
                result = codec.decode(resp.read())
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1)
            continue

        if "token" in result:
            ws_url = (result.get("url") or "https://server-v2.pixelgun3dserver.com/socket.io/")
            ws_url = ws_url.replace("https://", "wss://").replace("http://", "ws://")
            return {"token": result["token"], "ws_base_url": ws_url}

        if result.get("err") == "banned: 1":
            raise RuntimeError(f"Account banned (err_code={result.get('err_code')})")

        if attempt < max_retries - 1:
            time.sleep(1)

    return None
