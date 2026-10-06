"""Crypto primitives for PG3D Socket.IO protocol.

Ported from https://github.com/0xdsyx/pg3d-websocket-client
AES-CFB encryption, PBKDF2 key derivation, custom HTTPS codec, packet codec.
"""
from __future__ import annotations

import hashlib
import json
from random import randint

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class KeyDerivation:
    @staticmethod
    def master_key(sid: str) -> bytes:
        chars = [sid[p % len(sid)] for p in range(32)]
        r = "".join(chars).encode("utf-8")
        limit = min(len(r), 32)

        v1 = [0] * 32
        for p in range(limit):
            v1[p] = (p + 1) * r[p]

        v2 = [0] * 32
        for p in range(32):
            if (p + 1) % 3 != 0:
                v2[p] = v1[p]
            else:
                v2[p] = v1[p] + (v1[p - 1] % 7)

        v3 = [0] * 32
        for p in range(32):
            if p <= 15:
                v3[p] = v2[15 - p]
            else:
                v3[p] = v2[47 - p]

        v4 = [0] * 32
        for p in range(32):
            if p < 16:
                v4[p] = v3[p]
            else:
                v4[p] = v3[p] + v3[31 - p]

        v5 = [v4[p] & 255 for p in range(32)]

        res = bytearray(32)
        c = 0
        for p in range(32):
            res[p] = (v5[p] + c) & 255

        return bytes(res)

    @staticmethod
    def return_value(token: str) -> str:
        v1 = token.upper()[::-1]
        v2 = bytearray()
        for char in v1:
            for digit in str(ord(char)):
                v2.append(ord(digit))

        total = len(v2)
        if total % 2 != 0:
            mid = total // 2
            v2[mid], v2[total - 1] = v2[total - 1], v2[mid]

        half = total // 2
        left_half = v2[:half]
        right_half = v2[half:]

        res = []
        for left, right in zip(left_half, right_half):
            val = (left - 48) * 2 + (right - 48) // 2 + 56
            res.append(chr(val))
        return "".join(res)


class AesFacade:
    _SALT = bytes.fromhex("4C82A11824641596")

    def __init__(self, master_key: bytes):
        derived = hashlib.pbkdf2_hmac("sha1", master_key, self._SALT, 1, dklen=48)
        self._key = derived[:32]
        self._iv = derived[32:48]

    def decrypt(self, data: bytes) -> bytes:
        raw = data[1:] if data.startswith(b"\x04") else data
        cipher = Cipher(algorithms.AES(self._key), modes.CFB(self._iv))
        plain = cipher.decryptor().update(raw) + cipher.decryptor().finalize()
        # re-decrypt in one shot (decryptor is stateful)
        plain = Cipher(algorithms.AES(self._key), modes.CFB(self._iv)).decryptor().update(raw)
        if len(plain) > 0:
            pad = plain[-1]
            if 0 < pad <= 16 and len(plain) % 16 == 0 and plain[-pad:] == bytes([pad] * pad):
                return plain[:-pad]
        return plain

    def encrypt(self, data: bytes) -> bytes:
        pad = 16 - len(data) % 16
        padded = data + bytes([pad] * pad)
        return Cipher(algorithms.AES(self._key), modes.CFB(self._iv)).encryptor().update(padded)


class HttpsCodec:
    def decode(self, data: bytes) -> dict:
        result = {}
        if len(data) < 2:
            return result
        shift = int.from_bytes(data[-2:], "little")
        words = [int.from_bytes(data[pos:pos + 2], "little") for pos in range(0, len(data) - 2, 2)]
        pos = 0
        while pos < len(words):
            if pos + 2 > len(words):
                break
            key_len = words[pos] >> 1
            val_len = words[pos + 1] >> 1
            ks = pos + 2
            vs = ks + key_len
            ve = vs + val_len
            if ve > len(words):
                break
            key = bytes([(w - shift) & 255 for w in words[ks:ks + key_len]]).decode("utf-8", errors="ignore")
            val = bytes([(w - shift) & 255 for w in words[vs:ve]]).decode("utf-8", errors="ignore")
            result[key] = val
            pos = ve
        return result

    def encode(self, data: dict) -> bytes:
        raw = b""
        shift = randint(1, 255)
        for key, val in data.items():
            k, v = str(key), str(val)
            head = bytes([(len(k) << 1) % 256, (len(v) << 1) % 256])
            body = bytes([(b + shift) % 256 for b in (k.encode() + v.encode())])
            raw += head + body
        joined = b"\x00".join(bytes((b,)) for b in raw)
        return joined + b"\x00" + bytes((shift,)) + b"\x00"


class PacketCodec:
    PREFIX = b"452-/sio,"

    def __init__(self, aes: AesFacade):
        self._aes = aes

    def decode(self, frames: list) -> list:
        items = list(frames)
        header = items.pop(0)
        if isinstance(header, str):
            header = header.encode("utf-8")
        body = header[len(self.PREFIX):] if header.startswith(self.PREFIX) else header
        packet = json.loads(body)

        for i, entry in enumerate(packet):
            if not isinstance(entry, dict) or not entry.get("_placeholder") or "num" not in entry:
                continue
            slot = int(entry["num"])
            if slot < 0 or slot >= len(items):
                continue
            raw = items[slot]
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            if raw.startswith(b"\x04"):
                raw = raw[1:]
            plain = self._aes.decrypt(raw)
            try:
                packet[i] = json.loads(plain)
            except (json.JSONDecodeError, UnicodeDecodeError):
                try:
                    packet[i] = plain.decode("utf-8")
                except Exception:
                    packet[i] = plain
        return packet

    def encode(self, packet: list) -> list:
        placeholders = [{"_placeholder": True, "num": i} for i in range(len(packet))]
        head = self.PREFIX + json.dumps(placeholders, separators=(",", ":")).encode()
        frames = [head]
        for item in packet:
            if isinstance(item, dict):
                raw = json.dumps(item).encode()
            elif isinstance(item, str):
                raw = item.encode()
            elif isinstance(item, bytes):
                raw = item
            else:
                raw = str(item).encode()
            frames.append(b"\x04" + self._aes.encrypt(raw))
        return frames
