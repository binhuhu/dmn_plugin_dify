"""S-FEEL unary tests and literals: the decision-table cell language of DMN.

Supported in input entries (DMN 1.3 S-FEEL "simple unary tests"):

    -                      any value (also an empty cell)
    5   "gold"   true      equality with a literal
    date("2026-01-31")     date literal
    < 5   <= 5   > 5   >= 5
    [1..10]  ]1..10[  (1..10)  [1..10)   ranges, open or closed at each end
    "a", "b", > 100        a list: matches if any item matches
    not("a", "b")          negation of a list
    null                   matches a missing / null input

Not supported (refused with UNSUPPORTED_EXPRESSION, never guessed): variable
references as endpoints, arithmetic, functions other than date(), the FEEL
``?`` syntax and ``!=`` (write ``not(x)``).

Output entries are JSON values in the JSON format; in DMN XML they are FEEL
literals (strings, numbers, booleans, null, date("…")), parsed by ``parse_literal``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .errors import DmnError
from .sets import ALL_KINDS, ValueSet
from .values import compare, decimal_from_literal, equal, kind, parse_date

MAX_ENTRY_CHARS = 2048

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<string>"(?:[^"\\]|\\.)*")
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<op><=|>=|!=|\.\.|<|>|=|\(|\)|\[|\]|,|-)
  | (?P<name>[A-Za-z_À-￿][A-Za-z0-9_À-￿]*)
    """,
    re.VERBOSE,
)


@dataclass
class Tok:
    type: str
    text: str
    pos: int


def tokenize(text: str) -> list[Tok]:
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise DmnError("INVALID_ENTRY", f"无法识别的字符 {text[pos]!r}（位置 {pos + 1}）")
        if m.lastgroup != "ws":
            out.append(Tok(m.lastgroup, m.group(), pos))
        pos = m.end()
    return out


# ---------------------------------------------------------------- tests


class Test:
    """A unary test. ``evaluate`` → True / False / None (unknown, never matches)."""

    text: str = ""

    def evaluate(self, value):  # pragma: no cover - abstract
        raise NotImplementedError

    def value_set(self, universe: ValueSet) -> ValueSet:  # pragma: no cover
        raise NotImplementedError

    def literals(self):
        return []

    def ordering_kinds(self) -> set:
        return set()


class AnyTest(Test):
    def evaluate(self, value):
        return True

    def value_set(self, universe):
        return universe


@dataclass
class NullTest(Test):
    def evaluate(self, value):
        return value is None

    def value_set(self, universe):
        return ValueSet.only_null().intersect(universe)

    def null_result(self):
        return True


@dataclass
class EqTest(Test):
    value: object

    def evaluate(self, value):
        if value is None:
            return False
        return equal(value, self.value)

    def value_set(self, universe):
        return ValueSet.point(kind(self.value), self.value).intersect(universe)

    def literals(self):
        return [self.value]

    def null_result(self):
        return False


@dataclass
class CmpTest(Test):
    op: str
    value: object

    def evaluate(self, value):
        if value is None:
            return None
        c = compare(value, self.value)
        if c is None:
            return None
        return {"<": c < 0, "<=": c <= 0, ">": c > 0, ">=": c >= 0}[self.op]

    def value_set(self, universe):
        k, v = kind(self.value), self.value
        iv = {
            "<": (None, False, v, False),
            "<=": (None, False, v, True),
            ">": (v, False, None, False),
            ">=": (v, True, None, False),
        }[self.op]
        return ValueSet.interval(k, *iv).intersect(universe)

    def literals(self):
        return [self.value]

    def ordering_kinds(self):
        return {kind(self.value)}

    def null_result(self):
        return None


@dataclass
class RangeTest(Test):
    lo: object
    lo_closed: bool
    hi: object
    hi_closed: bool

    def evaluate(self, value):
        if value is None:
            return None
        a, b = compare(value, self.lo), compare(value, self.hi)
        if a is None or b is None:
            return None
        return (a > 0 or (a == 0 and self.lo_closed)) and (b < 0 or (b == 0 and self.hi_closed))

    def value_set(self, universe):
        return ValueSet.interval(kind(self.lo), self.lo, self.lo_closed, self.hi, self.hi_closed).intersect(universe)

    def literals(self):
        return [self.lo, self.hi]

    def ordering_kinds(self):
        return {kind(self.lo)}

    def null_result(self):
        return None


@dataclass
class AnyOf(Test):
    """A comma list of positive tests (disjunction)."""

    items: list

    def evaluate(self, value):
        unknown = False
        for t in self.items:
            r = t.evaluate(value)
            if r is True:
                return True
            if r is None:
                unknown = True
        return None if unknown else False

    def value_set(self, universe):
        s = ValueSet()
        for t in self.items:
            s = s.union(t.value_set(universe))
        return s

    def literals(self):
        return [v for t in self.items for v in t.literals()]

    def ordering_kinds(self):
        return set().union(*(t.ordering_kinds() for t in self.items))

    def null_result(self):
        rs = [t.null_result() for t in self.items]
        if True in rs:
            return True
        return None if None in rs else False


@dataclass
class NotTest(Test):
    inner: AnyOf

    def evaluate(self, value):
        r = self.inner.evaluate(value)
        return None if r is None else not r

    def value_set(self, universe):
        # Values where the inner list is definitely False. An ordering test of
        # another kind makes the inner result unknown (not False) for every
        # value outside the list, so that kind contributes nothing.
        positive = self.inner.value_set(universe)
        ordering = self.inner.ordering_kinds()
        parts = {}
        for k, ivs in universe.parts.items():
            if ordering - {k}:
                continue
            rest = ValueSet({k: ivs}).minus(positive)
            if rest.parts.get(k):
                parts[k] = rest.parts[k]
        null_ok = universe.null and self.inner.null_result() is False
        return ValueSet(parts, null_ok)

    def literals(self):
        return self.inner.literals()


# ---------------------------------------------------------------- parser


class _Parser:
    def __init__(self, text: str):
        if len(text) > MAX_ENTRY_CHARS:
            raise DmnError("INVALID_ENTRY", f"单元格超过 {MAX_ENTRY_CHARS} 字符")
        self.text = text
        self.toks = tokenize(text)
        self.i = 0

    def peek(self, offset=0):
        j = self.i + offset
        return self.toks[j] if j < len(self.toks) else None

    def take(self, *texts):
        t = self.peek()
        if t is None:
            want = " 或 ".join(texts) if texts else "更多内容"
            raise DmnError("INVALID_ENTRY", f"表达式意外结束，缺少 {want}")
        if texts and t.text not in texts:
            raise DmnError("INVALID_ENTRY", f"位置 {t.pos + 1} 处应为 {' 或 '.join(texts)}，实际是 {t.text!r}")
        self.i += 1
        return t

    def done(self):
        t = self.peek()
        if t is not None:
            raise DmnError("INVALID_ENTRY", f"位置 {t.pos + 1} 处多余的内容 {t.text!r}")

    def endpoint(self):
        t = self.take()
        if t.text == "-" and self.peek() is not None and self.peek().type == "number":
            return -decimal_from_literal(self.take().text)
        if t.type == "number":
            return decimal_from_literal(t.text)
        if t.type == "string":
            try:
                return json.loads(t.text)
            except ValueError as exc:
                raise DmnError("INVALID_ENTRY", f"字符串 {t.text} 含无效的转义") from exc
        if t.type == "name":
            if t.text == "true":
                return True
            if t.text == "false":
                return False
            if t.text == "date":
                self.take("(")
                s = self.take()
                if s.type != "string":
                    raise DmnError("INVALID_ENTRY", 'date() 需要一个 "YYYY-MM-DD" 字符串')
                d = parse_date(json.loads(s.text))
                if d is None:
                    raise DmnError("INVALID_ENTRY", f"{s.text} 不是有效的 YYYY-MM-DD 日期")
                self.take(")")
                return d
            if t.text == "null":
                raise DmnError("INVALID_ENTRY", "null 不能作为比较或区间的端点")
            raise DmnError(
                "UNSUPPORTED_EXPRESSION",
                f"单元格只支持字面量；变量引用 {t.text!r} 等 FEEL 表达式不受支持",
            )
        raise DmnError("INVALID_ENTRY", f"位置 {t.pos + 1} 处应为字面量，实际是 {t.text!r}")

    def positive(self) -> Test:
        t = self.peek()
        if t is None:
            raise DmnError("INVALID_ENTRY", "列表中有空项")
        if t.text in ("<", "<=", ">", ">="):
            self.i += 1
            v = self.endpoint()
            if kind(v) not in ("number", "string", "date"):
                raise DmnError("INVALID_ENTRY", f"{t.text} 只能比较数字、字符串或日期")
            return CmpTest(t.text, v)
        if t.text == "=":
            self.i += 1
            return EqTest(self.endpoint())
        if t.text == "!=":
            raise DmnError("UNSUPPORTED_EXPRESSION", "S-FEEL 没有 !=，请写 not(值)")
        if t.text in ("[", "]", "("):
            self.i += 1
            lo_closed = t.text == "["
            lo = self.endpoint()
            self.take("..")
            hi = self.endpoint()
            end = self.take("]", "[", ")")
            hi_closed = end.text == "]"
            if kind(lo) != kind(hi) or kind(lo) not in ("number", "string", "date"):
                raise DmnError("INVALID_ENTRY", "区间两端必须是同类的数字、字符串或日期")
            return RangeTest(lo, lo_closed, hi, hi_closed)
        if t.type == "name" and t.text == "null":
            self.i += 1
            return NullTest()
        return EqTest(self.endpoint())

    def positives(self) -> AnyOf:
        items = [self.positive()]
        while self.peek() is not None and self.peek().text == ",":
            self.i += 1
            items.append(self.positive())
        return AnyOf(items)


def parse_unary_tests(text) -> Test:
    if not isinstance(text, str):
        raise DmnError("INVALID_ENTRY", "输入条件必须是字符串（S-FEEL 一元测试）")
    s = text.strip()
    if s in ("", "-"):
        t = AnyTest()
        t.text = "-"
        return t
    p = _Parser(s)
    first = p.peek()
    if first.type == "name" and first.text == "not" and p.peek(1) is not None and p.peek(1).text == "(":
        p.i += 2
        inner = p.positives()
        p.take(")")
        p.done()
        t = NotTest(inner)
    else:
        t = p.positives()
        p.done()
        if len(t.items) == 1:
            t = t.items[0]
    t.text = s
    return t


def parse_literal(text):
    """A single FEEL literal (DMN XML output entries, default outputs)."""
    if not isinstance(text, str):
        raise DmnError("INVALID_ENTRY", "输出必须是 FEEL 字面量")
    s = text.strip()
    if s in ("", "null"):
        return None
    p = _Parser(s)
    v = p.endpoint()
    p.done()
    return v


def parse_literal_list(text):
    """A comma list of literals (DMN outputValues / allowed values)."""
    s = (text or "").strip()
    if not s:
        return []
    p = _Parser(s)
    out = [p.endpoint()]
    while p.peek() is not None and p.peek().text == ",":
        p.i += 1
        out.append(p.endpoint())
    p.done()
    return out


def column_universe(declared: str, allowed: Test | None) -> ValueSet:
    kinds = ALL_KINDS if declared == "any" else (declared,)
    u = ValueSet.universe(kinds, allow_null=True)
    if allowed is not None and not isinstance(allowed, AnyTest):
        u = allowed.value_set(u).union(ValueSet.only_null())
    return u
