"""Evaluate one decision table against one input object.

Hit policies (DMN 1.3 §8.2.11):

- UNIQUE     at most one rule may match; two or more → HIT_POLICY_VIOLATION
- ANY        several may match only if their outputs are identical
- PRIORITY   the matching rule whose outputs rank highest in the output
             ``values`` lists (columns compared left to right; ties → rule order)
- FIRST      the first matching rule in table order
- RULE ORDER all matches, in table order
- OUTPUT ORDER all matches, sorted by output priority (ties → rule order)
- COLLECT    all matches, in table order; with SUM / MIN / MAX / COUNT, one
             value. SUM and COUNT use distinct values by default (the DMN text);
             ``collect_values: "all"`` uses every match (Camunda 7 behaviour).

No match: single-hit tables return the default outputs when declared, else
null; RULE ORDER / OUTPUT ORDER / COLLECT return []; COUNT returns 0;
SUM / MIN / MAX return null.

Missing inputs are null. A test that cannot be decided (a comparison with null
or with a value of another type) is "unknown" and never matches; the trace and
``missing_inputs`` / ``type_mismatches`` say why.
"""

from __future__ import annotations

from decimal import Decimal

from .canonical import table_sha256
from .errors import DmnError
from .model import Table, coerce_object, load_table
from .values import compare, describe, from_json, same_output, to_json

SCHEMA = "dmn-plugin/evaluation.v1"
MAX_INPUT_BYTES = 256 * 1024
_MISSING = object()


def _lookup(obj, path):
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return _MISSING
    return cur


def _shape(table: Table, values: list):
    """One rule's outputs: a bare value for one output column, else an object."""
    if len(table.outputs) == 1:
        return values[0]
    return {o.name: v for o, v in zip(table.outputs, values)}


def _priority_key(table: Table, rule):
    key = []
    for o, v in zip(table.outputs, rule.outputs):
        if o.values:
            idx = next((i for i, x in enumerate(o.values) if same_output(v, x)), len(o.values))
            key.append(idx)
    return (*key, rule.index)


def _distinct(values):
    out = []
    for v in values:
        if not any(same_output(v, x) for x in out):
            out.append(v)
    return out


def _aggregate(table: Table, values: list):
    agg = table.aggregation
    vals = [v for v in values if v is not None]
    if table.collect_values == "distinct" and agg in ("SUM", "COUNT"):
        vals = _distinct(vals)
    if agg == "COUNT":
        return Decimal(len(vals))
    if not vals:
        return None
    if agg == "SUM":
        if not all(isinstance(v, Decimal) for v in vals):
            raise DmnError("AGGREGATION_ERROR", "COLLECT SUM 只能对数字求和")
        return sum(vals, Decimal(0))
    best = vals[0]
    for v in vals[1:]:
        c = compare(v, best)
        if c is None:
            raise DmnError("AGGREGATION_ERROR", f"COLLECT {agg} 遇到无法比较的值 {describe(v)} 与 {describe(best)}")
        if (agg == "MIN" and c < 0) or (agg == "MAX" and c > 0):
            best = v
    return best


def evaluate(table_raw, inputs_raw, *, decision_id=None, expected_sha256=None, trace=True) -> dict:
    """Never raises: every outcome, including refusals, is an envelope."""
    env = {
        "schema": SCHEMA,
        "status": "ERROR",
        "decision": None,
        "result": None,
        "outputs": None,
        "matched_rules": [],
        "selected_rules": [],
        "missing_inputs": [],
        "type_mismatches": [],
        "inputs_used": {},
        "trace": [] if trace else None,
        "error": None,
    }
    try:
        table = table_raw if isinstance(table_raw, Table) else load_table(table_raw, decision_id)
        normalized = table.normalized()
        digest = table_sha256(normalized)
        env["decision"] = {
            "id": table.id,
            "name": table.name,
            "version": table.version,
            "hit_policy": table.hit_policy,
            "aggregation": table.aggregation,
            "sha256": digest,
            "source": table.source,
        }
        if table.problems:
            first = table.problems[0]
            more = len(table.problems) - 1
            raise DmnError(
                "INVALID_TABLE", first.message + (f"（另有 {more} 个问题，用 check_decision_table 查看全部）" if more else ""), first.where
            )
        if expected_sha256 is not None and str(expected_sha256).strip():
            want = str(expected_sha256).strip().lower()
            if want != digest:
                raise DmnError("PIN_MISMATCH", f"决策表 sha256 {digest} 与固定值 {want} 不一致；表已被修改或固定值过期")
        inputs = coerce_object(inputs_raw, "inputs", MAX_INPUT_BYTES)
        return _run(table, inputs, env, trace)
    except DmnError as e:
        env["status"] = "ERROR"
        env["error"] = e.as_dict()
        return env
    except Exception as e:  # noqa: BLE001 - last resort: a workflow gets an envelope, never a stack trace
        env["status"] = "ERROR"
        env["result"] = env["outputs"] = None
        env["error"] = {"code": "INTERNAL_ERROR", "message": f"引擎内部错误（{type(e).__name__}），请把决策表和输入报告给插件维护者"}
        return env


def _run(table: Table, inputs: dict, env: dict, trace: bool) -> dict:
    values = []
    for inp in table.inputs:
        raw = _lookup(inputs, inp.path)
        if raw is _MISSING:
            env["missing_inputs"].append(inp.expression)
            raw = None
        v, mismatch = from_json(raw, inp.type)
        if mismatch:
            env["type_mismatches"].append({"input": inp.id, "expected": inp.type, "value": describe(raw)})
        values.append(v)
        env["inputs_used"][inp.id] = to_json(v) if not mismatch else raw
    matched = []
    for rule in table.rules:
        results = [t.evaluate(v) for t, v in zip(rule.tests, values)]
        ok = all(r is True for r in results)
        if ok:
            matched.append(rule)
        if trace:
            row = {"rule": rule.id, "matched": ok}
            failed = [
                {"input": table.inputs[i].id, "test": rule.tests[i].text, "result": "unknown" if r is None else "false"}
                for i, r in enumerate(results)
                if r is not True
            ]
            if failed:
                row["failed"] = failed[:4] if len(failed) > 4 else failed
            env["trace"].append(row)
    env["matched_rules"] = [r.id for r in matched]
    policy = table.hit_policy

    if table.single_hit:
        chosen = None
        if policy == "UNIQUE" and len(matched) > 1:
            raise_violation(env, "UNIQUE 表同时命中多条规则：" + "、".join(r.id for r in matched))
            return env
        if policy == "ANY" and len(matched) > 1:
            first = matched[0]
            for r in matched[1:]:
                if not all(same_output(a, b) for a, b in zip(first.outputs, r.outputs)):
                    raise_violation(env, f"ANY 表命中的规则 {first.id} 与 {r.id} 输出不同")
                    return env
        if matched:
            chosen = min(matched, key=lambda r: _priority_key(table, r)) if policy == "PRIORITY" else matched[0]
            env["selected_rules"] = [chosen.id]
            out = chosen.outputs
            env["status"] = "MATCHED"
        elif any(o.has_default for o in table.outputs):
            out = [o.default if o.has_default else None for o in table.outputs]
            env["status"] = "DEFAULT"
        else:
            out = [None] * len(table.outputs)
            env["status"] = "NO_MATCH"
        env["result"] = to_json(_shape(table, out)) if (matched or env["status"] == "DEFAULT") else None
        env["outputs"] = {o.name: to_json(v) for o, v in zip(table.outputs, out)}
        return env

    hits = sorted(matched, key=lambda r: _priority_key(table, r)) if policy == "OUTPUT ORDER" else matched
    env["selected_rules"] = [r.id for r in hits]
    env["status"] = "MATCHED" if hits else "NO_MATCH"
    if table.aggregation:
        try:
            value = _aggregate(table, [r.outputs[0] for r in hits])
        except DmnError as e:
            env["status"] = "ERROR"
            env["error"] = e.as_dict()
            return env
        env["result"] = to_json(value)
        env["outputs"] = {table.outputs[0].name: to_json(value)}
        return env
    env["result"] = [to_json(_shape(table, r.outputs)) for r in hits]
    env["outputs"] = {o.name: [to_json(r.outputs[c]) for r in hits] for c, o in enumerate(table.outputs)}
    return env


def raise_violation(env, message):
    env["status"] = "ERROR"
    env["error"] = {"code": "HIT_POLICY_VIOLATION", "message": message}
    env["result"] = None
    env["outputs"] = None
