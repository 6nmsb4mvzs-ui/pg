"""
Small Valve KeyValues (VDF) reader/writer used only as a startup fallback when
third-party `vdf` is unavailable.
"""
from __future__ import annotations
import re

_TOKEN = re.compile(r'''
    "(?:\\.|[^"\\])*"     |
    [\{\}]                  |
    [^\s\{\}"]+
''', re.VERBOSE)

def _strip_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        buf = []
        i = 0
        in_quote = False
        esc = False
        while i < len(line):
            ch = line[i]
            if esc:
                buf.append(ch); esc = False; i += 1; continue
            if ch == "\\" and in_quote:
                buf.append(ch); esc = True; i += 1; continue
            if ch == '"':
                in_quote = not in_quote
                buf.append(ch); i += 1; continue
            if not in_quote and ch == "/" and i + 1 < len(line) and line[i+1] == "/":
                break
            buf.append(ch); i += 1
        out.append("".join(buf))
    return "\n".join(out)

def _decode(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        body = tok[1:-1]
        body = body.replace(r'\\"', '"').replace(r'\\\\', '\\')
        return body
    return tok

def loads(text: str):
    tokens = [_decode(t) if t not in ("{", "}") else t
              for t in _TOKEN.findall(_strip_comments(text))]
    pos = 0
    def obj(stop_on_brace=False):
        nonlocal pos
        d = {}
        while pos < len(tokens):
            if tokens[pos] == "}":
                if stop_on_brace:
                    pos += 1
                    return d
                pos += 1
                continue
            key = tokens[pos]; pos += 1
            if pos >= len(tokens):
                d[key] = ""; break
            if tokens[pos] == "{":
                pos += 1
                value = obj(True)
            else:
                value = tokens[pos]; pos += 1
            d[key] = value
        return d
    return obj(False)

def load(fp):
    return loads(fp.read())

def _q(value):
    s = str(value).replace("\\", "\\\\").replace('"', r'\\"')
    return '"' + s + '"'

def dumps(data, pretty=True):
    lines = []
    def emit(d, depth=0):
        ind = "\t" * depth
        for k, v in d.items():
            if isinstance(v, dict):
                lines.append(f"{ind}{_q(k)}")
                lines.append(f"{ind}{{")
                emit(v, depth + 1)
                lines.append(f"{ind}}}")
            else:
                lines.append(f"{ind}{_q(k)}\t\t{_q(v)}")
    emit(data)
    return "\n".join(lines) + "\n"

def dump(data, fp, pretty=True):
    fp.write(dumps(data, pretty=pretty))
