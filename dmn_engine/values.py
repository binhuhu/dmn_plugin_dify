"""FEEL-style values for decision tables.

Kinds: ``number`` (Decimal, so sums do not drift), ``string``, ``boolean``,
``date`` (datetime.date) and null (None). JSON in, JSON out.

Comparison rules (used by the unary tests):
- equality across different kinds is False, never an error;
- ordering (<, <=, >, >=, ranges) is defined within numbers, strings and dates;
  across kinds, or with booleans, the result is unknown (None), which never
  matches a rule.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import re
from decimal import Decimal, InvalidOperation

TYPES = ("number", "string", "boolean", "date", "any")
TYPE_ALIASES = {
    "number": "number",
    "integer": "number",
    "int": "number",
    "long": "number",
    "double": "number",
    "decimal": "number",
    "float": "number",
    "string": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "any": "any",
    "": "any",
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ORDERED_KINDS = ("number", "string", "date")


def normalize_type(raw) -> str | None:
    """Map a DMN/FEEL typeRef (``feel:number``, ``integer``…) to an engine type."""
    if raw is None:
        return "any"
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if ":" in name:
        name = name.split(":", 1)[1]
    return TYPE_ALIASES.get(name.lower())


def kind(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Decimal):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, _dt.date):
        return "date"
    return "other"


def parse_date(text: str) -> _dt.date | None:
    if not isinstance(text, str) or not _DATE.match(text):
        return None
    try:
        return _dt.date.fromisoformat(text)
    except ValueError:
        return None


def to_number(value) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(repr(value))
    if isinstance(value, Decimal):
        return value
    return None


def from_json(value, declared: str = "any"):
    """Convert a JSON input value to a FEEL value for an input of ``declared`` type.

    Returns ``(feel_value, mismatch)``; ``mismatch`` is True when the JSON value
    does not fit the declared type (the value is then passed through as its
    natural kind, so equality tests still behave predictably).
    """
    if value is None:
        return None, False
    if isinstance(value, bool):
        return value, declared not in ("boolean", "any")
    if isinstance(value, (int, float)):
        n = to_number(value)
        return n, declared not in ("number", "any")
    if isinstance(value, str):
        if declared == "date":
            d = parse_date(value)
            return (d, False) if d is not None else (value, True)
        return value, declared not in ("string", "any")
    # Lists and objects are not decision-table cell values.
    return ("<" + type(value).__name__ + ">"), True


def to_json(value):
    """FEEL value → JSON-friendly value (Decimal → int/float, date → ISO)."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value() and abs(value) < Decimal(2) ** 53:
            return int(value)
        return float(value)
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, list):
        return [to_json(v) for v in value]
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    return value


def output_value(value, declared: str):
    """Validate/normalize an output entry (JSON) for an output column.

    Returns the FEEL value or raises ValueError with a reason.
    """
    if value is None:
        return None
    if declared == "any":
        if isinstance(value, (list, dict)):
            return value  # passed through untouched (JSON outputs are allowed)
        v, _ = from_json(value, "any")
        return v
    if declared == "number":
        n = to_number(value)
        if n is None:
            raise ValueError("应为数字")
        return n
    if declared == "string":
        if not isinstance(value, str):
            raise ValueError("应为字符串")
        return value
    if declared == "boolean":
        if not isinstance(value, bool):
            raise ValueError("应为 true/false")
        return value
    if declared == "date":
        d = parse_date(value) if isinstance(value, str) else None
        if d is None:
            raise ValueError("应为 YYYY-MM-DD 日期")
        return d
    raise ValueError("未知类型")


def equal(a, b) -> bool:
    ka, kb = kind(a), kind(b)
    if ka != kb:
        return False
    return a == b


def compare(a, b) -> int | None:
    """-1/0/1, or None when the two values have no defined order."""
    ka, kb = kind(a), kind(b)
    if ka != kb or ka not in ORDERED_KINDS:
        return None
    return (a > b) - (a < b)


def same_output(a, b) -> bool:
    """Output equality for ANY / distinct: structural, kind-aware."""
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return json.dumps(to_json(a), sort_keys=True) == json.dumps(to_json(b), sort_keys=True)
    return equal(a, b) or (a is None and b is None)


def describe(value) -> str:
    """Short human rendering for traces and messages."""
    j = to_json(value)
    try:
        text = json.dumps(j, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(j)
    return text if len(text) <= 120 else text[:117] + "..."


def decimal_from_literal(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - tokenizer guarantees digits
        raise ValueError(text) from exc
