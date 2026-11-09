"""RFC 8785 (JCS) canonical JSON and the table pin.

The pin is sha256 over the canonical form of the *normalized* table, so a
JSON table and the DMN XML it was imported from (or reformatted JSON) pin to
the same value. In JavaScript the same digest is
``sha256(canonicalize(normalized))`` with any JCS library.
"""

from __future__ import annotations

import hashlib
import json
import math

_SAFE = 2**53


def _number(x) -> str:
    if isinstance(x, bool):  # pragma: no cover - handled by caller
        raise TypeError
    if isinstance(x, int):
        if abs(x) > _SAFE:
            raise ValueError("整数超出 IEEE-754 安全范围（±2^53）")
        return str(x)
    if not math.isfinite(x):
        raise ValueError("不允许 NaN/Infinity")
    if x == 0:
        return "0"
    # Shortest round-trip digits (same as ECMAScript), then ES formatting.
    r = repr(abs(x))
    if "e" in r:
        mant, exp = r.split("e")
        exp = int(exp)
    else:
        mant, exp = r, 0
    if "." in mant:
        ip, fp = mant.split(".")
    else:
        ip, fp = mant, ""
    digits = (ip + fp).lstrip("0")
    # position of the decimal point relative to the start of `digits`
    point = len(ip) + exp - (len(ip + fp) - len((ip + fp).lstrip("0")))
    digits = digits.rstrip("0") or "0"
    k, n = len(digits), point
    sign = "-" if x < 0 else ""
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        es = ("+" if e >= 0 else "-") + str(abs(e))
        s = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + es
    return sign + s


def _utf16_key(s: str):
    return s.encode("utf-16-be")


def canonicalize(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ",".join(canonicalize(v) for v in value) + "]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: _utf16_key(kv[0]))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonicalize(v) for k, v in items) + "}"
    raise TypeError(f"不可规范化的值 {type(value).__name__}")


def table_sha256(normalized: dict) -> str:
    return hashlib.sha256(canonicalize(normalized).encode("utf-8")).hexdigest()
