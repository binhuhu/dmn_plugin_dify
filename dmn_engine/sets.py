"""Value sets for static analysis: unions of typed intervals plus a null flag.

A column's universe is the kinds its input type allows (``number`` → numbers
only, ``any`` → numbers, strings, dates, booleans) plus null. Every unary test
denotes the subset of that universe it matches, so overlap, subsumption and
contradiction become set questions:

- two rules overlap  ⇔ every column's sets intersect;
- rule B is shadowed by rule A ⇔ every column of B ⊆ the same column of A;
- a rule never matches ⇔ some column's set is empty.

Intervals are (lo, lo_closed, hi, hi_closed); ``None`` bounds are infinite.
Booleans are handled as the two points False < True.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ALL_KINDS = ("number", "string", "date", "boolean")


def _lower_le(a, b) -> bool:
    """Lower bound a <= lower bound b (a starts no later than b)."""
    (av, ac), (bv, bc) = a, b
    if av is None:
        return True
    if bv is None:
        return False
    if av != bv:
        return av < bv
    return ac or not bc


def _upper_ge(a, b) -> bool:
    """Upper bound a >= upper bound b (a ends no earlier than b)."""
    (av, ac), (bv, bc) = a, b
    if av is None:
        return True
    if bv is None:
        return False
    if av != bv:
        return av > bv
    return ac or not bc


def _empty(iv) -> bool:
    lo, lc, hi, hc = iv
    if lo is None or hi is None:
        return False
    if isinstance(lo, bool) and isinstance(hi, bool):
        # Only two values exist: an interval holds just its closed endpoints.
        return lo > hi or not (lc or hc) or (lo == hi and not (lc and hc))
    return lo > hi or (lo == hi and not (lc and hc))


def _intersect(a, b):
    lo = a[:2] if _lower_le(b[:2], a[:2]) else b[:2]
    hi = a[2:] if _upper_ge(b[2:], a[2:]) else b[2:]
    iv = (*lo, *hi)
    return None if _empty(iv) else iv


def _touch_or_overlap(a, b) -> bool:
    """a starts before b; can they be merged into one interval?"""
    ahi, ahc = a[2], a[3]
    blo, blc = b[0], b[1]
    if ahi is None or blo is None:
        return True
    if ahi > blo:
        return True
    if ahi == blo:
        return ahc or blc
    return False


def _sort_key(iv):
    lo, lc = iv[0], iv[1]
    # None (−∞) first; closed before open at the same value.
    return (lo is not None, lo if lo is not None else 0, not lc)


def _normalize(ivs):
    ivs = [iv for iv in ivs if not _empty(iv)]
    if not ivs:
        return []
    try:
        ivs.sort(key=_sort_key)
    except TypeError:  # pragma: no cover - kinds are kept apart by the caller
        return ivs
    out = [ivs[0]]
    for iv in ivs[1:]:
        cur = out[-1]
        if _touch_or_overlap(cur, iv):
            hi = cur[2:] if _upper_ge(cur[2:], iv[2:]) else iv[2:]
            out[-1] = (cur[0], cur[1], *hi)
        else:
            out.append(iv)
    return out


def _subtract(a, bs):
    """Interval a minus the union of normalized intervals bs."""
    pieces = [a]
    for b in bs:
        nxt = []
        for p in pieces:
            if _intersect(p, b) is None:
                nxt.append(p)
                continue
            # left remainder: [p.lo, b.lo)
            if b[0] is not None:
                left = (p[0], p[1], b[0], not b[1])
                if not _empty(left) and _lower_le(p[:2], (b[0], not b[1])):
                    nxt.append(left)
            # right remainder: (b.hi, p.hi]
            if b[2] is not None:
                right = (b[2], not b[3], p[2], p[3])
                if not _empty(right) and _upper_ge(p[2:], (b[2], not b[3])):
                    nxt.append(right)
        pieces = nxt
    return _normalize(pieces)


FULL = (None, False, None, False)
BOOL_FULL = (False, True, True, True)


@dataclass
class ValueSet:
    parts: dict = field(default_factory=dict)  # kind -> normalized interval list
    null: bool = False

    @staticmethod
    def universe(kinds, allow_null: bool = True) -> "ValueSet":
        return ValueSet({k: [BOOL_FULL if k == "boolean" else FULL] for k in kinds}, allow_null)

    @staticmethod
    def point(k: str, v) -> "ValueSet":
        return ValueSet({k: [(v, True, v, True)]}, False)

    @staticmethod
    def interval(k: str, lo, lc, hi, hc) -> "ValueSet":
        return ValueSet({k: _normalize([(lo, lc, hi, hc)])}, False)

    @staticmethod
    def only_null() -> "ValueSet":
        return ValueSet({}, True)

    def is_empty(self) -> bool:
        return not self.null and not any(self.parts.values())

    def union(self, other: "ValueSet") -> "ValueSet":
        parts = {k: list(v) for k, v in self.parts.items()}
        for k, ivs in other.parts.items():
            parts[k] = _normalize(parts.get(k, []) + list(ivs))
        return ValueSet(parts, self.null or other.null)

    def intersect(self, other: "ValueSet") -> "ValueSet":
        parts = {}
        for k in self.parts.keys() & other.parts.keys():
            got = []
            for a in self.parts[k]:
                for b in other.parts[k]:
                    iv = _intersect(a, b)
                    if iv:
                        got.append(iv)
            if got:
                parts[k] = _normalize(got)
        return ValueSet(parts, self.null and other.null)

    def minus(self, other: "ValueSet") -> "ValueSet":
        parts = {}
        for k, ivs in self.parts.items():
            sub = other.parts.get(k, [])
            got = []
            for a in ivs:
                got.extend(_subtract(a, sub))
            if got:
                parts[k] = _normalize(got)
        return ValueSet(parts, self.null and not other.null)

    def subset_of(self, other: "ValueSet") -> bool:
        return self.minus(other).is_empty()

    def intersects(self, other: "ValueSet") -> bool:
        return not self.intersect(other).is_empty()

    def describe(self) -> str:
        from .values import to_json

        bits = []
        for k in sorted(self.parts):
            for lo, lc, hi, hc in self.parts[k]:
                if lo is not None and lo == hi:
                    bits.append(repr(to_json(lo)) if k != "string" else '"' + lo + '"')
                else:
                    a = "-∞" if lo is None else str(to_json(lo))
                    b = "+∞" if hi is None else str(to_json(hi))
                    bits.append(("[" if lc else "(") + a + ".." + b + ("]" if hc else ")"))
        if self.null:
            bits.append("null")
        return ", ".join(bits) if bits else "∅"
