"""Decision-table model: load, validate and normalize.

JSON format ``dmn-table/v1`` (one decision table, DMN semantics)::

    {
      "format": "dmn-table/v1",
      "id": "loan-risk",                       # required, 1..128 chars
      "name": "贷款风险",                       # optional
      "version": "1.0.0",                       # optional
      "hit_policy": "UNIQUE",                   # UNIQUE ANY PRIORITY FIRST
                                                # "RULE ORDER" "OUTPUT ORDER" COLLECT
                                                # or U A P F R O C C+ C< C> C#
      "aggregation": "SUM",                     # COLLECT only: SUM MIN MAX COUNT
      "collect_values": "distinct",             # SUM/COUNT: "distinct" (DMN spec,
                                                # default) or "all" (Camunda 7 style)
      "inputs": [
        {"id": "age", "label": "年龄", "expression": "applicant.age",
         "type": "number", "values": "[18..120]"}         # values: optional S-FEEL
      ],
      "outputs": [
        {"name": "risk", "type": "string",
         "values": ["HIGH", "MEDIUM", "LOW"],             # optional; priority order
         "default": "LOW"}                                 # optional default output
      ],
      "rules": [
        {"id": "r1", "when": ["< 25"], "then": ["HIGH"], "annotation": "…"}
      ]
    }

``load_table`` also accepts DMN 1.1–1.5 XML (see ``dmn_xml``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .errors import DmnError
from .sfeel import AnyOf, AnyTest, EqTest, NotTest, Test, column_universe, parse_unary_tests
from .values import describe, kind, normalize_type, output_value, same_output, to_json

FORMAT = "dmn-table/v1"
MAX_TABLE_BYTES = 1024 * 1024
MAX_INPUTS, MAX_OUTPUTS, MAX_RULES = 64, 32, 1000
HIT_POLICIES = ("UNIQUE", "ANY", "PRIORITY", "FIRST", "RULE ORDER", "OUTPUT ORDER", "COLLECT")
SINGLE_HIT = ("UNIQUE", "ANY", "PRIORITY", "FIRST")
AGGREGATIONS = ("SUM", "MIN", "MAX", "COUNT")
_SHORT = {
    "U": ("UNIQUE", None),
    "A": ("ANY", None),
    "P": ("PRIORITY", None),
    "F": ("FIRST", None),
    "R": ("RULE ORDER", None),
    "O": ("OUTPUT ORDER", None),
    "C": ("COLLECT", None),
    "C+": ("COLLECT", "SUM"),
    "C<": ("COLLECT", "MIN"),
    "C>": ("COLLECT", "MAX"),
    "C#": ("COLLECT", "COUNT"),
}
_NAME = re.compile(r"^[^\x00-\x1f]{1,128}$")
_PATH_PART = re.compile(r"^[^\x00-\x1f.]{1,128}$")


@dataclass
class Input:
    id: str
    label: str
    expression: str
    path: list
    type: str
    values_text: str | None
    allowed: Test | None
    universe: object = None


@dataclass
class Output:
    name: str
    type: str
    values: list | None  # FEEL values, priority order (first = highest)
    has_default: bool
    default: object = None


@dataclass
class Rule:
    id: str
    index: int
    tests: list
    outputs: list
    annotation: str | None = None


@dataclass
class Table:
    id: str
    name: str | None
    version: str | None
    hit_policy: str
    aggregation: str | None
    collect_values: str
    inputs: list
    outputs: list
    rules: list
    source: str = "json"
    problems: list = field(default_factory=list)  # DmnError found while loading

    @property
    def single_hit(self) -> bool:
        return self.hit_policy in SINGLE_HIT

    def normalized(self) -> dict:
        """Canonical JSON form (hashed for pins, returned by the check tool)."""
        out = {
            "format": FORMAT,
            "id": self.id,
            "hit_policy": self.hit_policy,
            "inputs": [
                {
                    k: v
                    for k, v in (
                        ("id", i.id),
                        ("label", i.label),
                        ("expression", i.expression),
                        ("type", i.type),
                        ("values", i.values_text),
                    )
                    if v is not None
                }
                for i in self.inputs
            ],
            "outputs": [],
            "rules": [],
        }
        if self.name is not None:
            out["name"] = self.name
        if self.version is not None:
            out["version"] = self.version
        if self.aggregation:
            out["aggregation"] = self.aggregation
            if self.aggregation in ("SUM", "COUNT"):
                out["collect_values"] = self.collect_values
        for o in self.outputs:
            d = {"name": o.name, "type": o.type}
            if o.values is not None:
                d["values"] = to_json(o.values)
            if o.has_default:
                d["default"] = to_json(o.default)
            out["outputs"].append(d)
        for r in self.rules:
            d = {"id": r.id, "when": [t.text for t in r.tests], "then": to_json(r.outputs)}
            if r.annotation:
                d["annotation"] = r.annotation
            out["rules"].append(d)
        return out


# ---------------------------------------------------------------- JSON parsing


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"重复的字段 {k!r}")
        out[k] = v
    return out


def _no_constants(name):
    raise ValueError(f"不允许 {name}")


def strict_json(text: str, label: str, limit: int):
    if len(text.encode("utf-8")) > limit:
        raise DmnError("TOO_LARGE", f"{label} 超过 {limit // 1024} KiB")
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constants)
    except (ValueError, RecursionError) as exc:
        raise DmnError("INVALID_JSON", f"{label} 不是有效的 JSON：{exc}") from exc


def coerce_object(raw, label: str, limit: int) -> dict:
    """Dify passes objects or JSON strings; accept both, bounded."""
    if isinstance(raw, dict):
        text = json.dumps(raw, ensure_ascii=False, allow_nan=False)
        return strict_json(text, label, limit)
    if isinstance(raw, str):
        value = strict_json(raw, label, limit)
        if not isinstance(value, dict):
            raise DmnError("INVALID_INPUT", f"{label} 必须是 JSON 对象")
        return value
    raise DmnError("INVALID_INPUT", f"{label} 必须是对象或 JSON 字符串")


def load_table(raw, decision_id: str | None = None) -> Table:
    """Load a table from a dict, a JSON string or DMN XML. Collects problems.

    Structural errors that leave nothing to analyse raise DmnError; cell-level
    problems are collected in ``table.problems`` so the check tool can list all
    of them. ``evaluate`` refuses a table that has any problem.
    """
    if isinstance(raw, str) and raw.lstrip().startswith("<"):
        from .dmn_xml import table_from_xml

        return table_from_xml(raw, decision_id)
    doc = coerce_object(raw, "table", MAX_TABLE_BYTES)
    return table_from_json(doc)


def _hit_policy(raw_policy, raw_agg, problems):
    if not isinstance(raw_policy, str) or not raw_policy.strip():
        problems.append(DmnError("INVALID_TABLE", "hit_policy 必填", "hit_policy"))
        return "UNIQUE", None
    p = raw_policy.strip().upper().replace("_", " ")
    agg = None
    if p in _SHORT:
        p, agg = _SHORT[p]
    if p not in HIT_POLICIES:
        problems.append(DmnError("INVALID_TABLE", f"未知的 hit_policy {raw_policy!r}，可用：{'、'.join(HIT_POLICIES)}", "hit_policy"))
        return "UNIQUE", None
    if raw_agg is not None:
        a = raw_agg.strip().upper() if isinstance(raw_agg, str) else raw_agg
        if a not in AGGREGATIONS:
            problems.append(DmnError("INVALID_TABLE", f"aggregation 只能是 {'、'.join(AGGREGATIONS)}", "aggregation"))
        elif agg and agg != a:
            problems.append(DmnError("INVALID_TABLE", f"hit_policy {raw_policy} 已含聚合，与 aggregation {a} 冲突", "aggregation"))
        else:
            agg = a
    if agg and p != "COLLECT":
        problems.append(DmnError("INVALID_TABLE", "aggregation 只能与 COLLECT 一起使用", "aggregation"))
        agg = None
    return p, agg


def _text(v, where, problems, required=True, label="字段"):
    if v is None and not required:
        return None
    if not isinstance(v, str) or not _NAME.match(v):
        problems.append(DmnError("INVALID_TABLE", f"{label}必须是 1–128 字符的字符串", where))
        return None
    return v


def table_from_json(doc: dict) -> Table:
    problems: list[DmnError] = []
    allowed_keys = {
        "format",
        "id",
        "name",
        "version",
        "hit_policy",
        "aggregation",
        "collect_values",
        "inputs",
        "outputs",
        "rules",
        "description",
    }
    for k in doc:
        if k not in allowed_keys:
            problems.append(DmnError("INVALID_TABLE", f"未知字段 {k!r}", k))
    if doc.get("format") != FORMAT:
        problems.append(DmnError("INVALID_TABLE", f"format 必须是 {FORMAT!r}", "format"))
    tid = _text(doc.get("id"), "id", problems, label="id ") or "table"
    name = _text(doc.get("name"), "name", problems, required=False, label="name ")
    version = _text(doc.get("version"), "version", problems, required=False, label="version ")
    policy, agg = _hit_policy(doc.get("hit_policy"), doc.get("aggregation"), problems)
    collect_values = doc.get("collect_values", "distinct")
    if collect_values not in ("distinct", "all"):
        problems.append(DmnError("INVALID_TABLE", "collect_values 只能是 distinct 或 all", "collect_values"))
        collect_values = "distinct"
    raw_inputs, raw_outputs, raw_rules = doc.get("inputs"), doc.get("outputs"), doc.get("rules")
    if not isinstance(raw_inputs, list) or len(raw_inputs) > MAX_INPUTS:
        raise DmnError("INVALID_TABLE", f"inputs 必须是 0–{MAX_INPUTS} 项的列表", "inputs")
    if not isinstance(raw_outputs, list) or not 1 <= len(raw_outputs) <= MAX_OUTPUTS:
        raise DmnError("INVALID_TABLE", f"outputs 必须是 1–{MAX_OUTPUTS} 项的列表", "outputs")
    if not isinstance(raw_rules, list) or len(raw_rules) > MAX_RULES:
        raise DmnError("INVALID_TABLE", f"rules 必须是 0–{MAX_RULES} 条的列表", "rules")

    inputs = []
    for i, d in enumerate(raw_inputs):
        w = f"inputs[{i}]"
        if not isinstance(d, dict):
            raise DmnError("INVALID_TABLE", "输入列必须是对象", w)
        for k in d:
            if k not in {"id", "label", "expression", "type", "values"}:
                problems.append(DmnError("INVALID_TABLE", f"未知字段 {k!r}", f"{w}.{k}"))
        expr = d.get("expression")
        iid = d.get("id") if d.get("id") is not None else expr
        iid = _text(iid, f"{w}.id", problems, label="输入列 id ") or f"input{i + 1}"
        inputs.append(make_input(iid, d.get("label"), expr, d.get("type"), d.get("values"), w, problems))
    outputs = []
    for i, d in enumerate(raw_outputs):
        w = f"outputs[{i}]"
        if not isinstance(d, dict):
            raise DmnError("INVALID_TABLE", "输出列必须是对象", w)
        for k in d:
            if k not in {"name", "type", "values", "default", "label"}:
                problems.append(DmnError("INVALID_TABLE", f"未知字段 {k!r}", f"{w}.{k}"))
        values = d.get("values")
        if values is not None and not isinstance(values, list):
            problems.append(DmnError("INVALID_TABLE", "values 必须是值的列表（按优先级从高到低）", f"{w}.values"))
            values = None
        outputs.append(make_output(d.get("name"), d.get("type"), values, "default" in d, d.get("default"), w, problems))
    rules = []
    for r, d in enumerate(raw_rules):
        w = f"rules[{r}]"
        if not isinstance(d, dict):
            raise DmnError("INVALID_TABLE", "规则必须是对象", w)
        for k in d:
            if k not in {"id", "when", "then", "annotation"}:
                problems.append(DmnError("INVALID_TABLE", f"未知字段 {k!r}", f"{w}.{k}"))
        when, then = d.get("when", []), d.get("then")
        if not isinstance(when, list) or len(when) != len(inputs):
            problems.append(DmnError("INVALID_TABLE", f"when 必须是与输入列等长（{len(inputs)}）的列表", f"{w}.when"))
            when = ["-"] * len(inputs)
        if not isinstance(then, list) or len(then) != len(outputs):
            problems.append(DmnError("INVALID_TABLE", f"then 必须是与输出列等长（{len(outputs)}）的列表", f"{w}.then"))
            then = [None] * len(outputs)
        annotation = d.get("annotation")
        if annotation is not None and not isinstance(annotation, str):
            problems.append(DmnError("INVALID_TABLE", "annotation 必须是字符串", f"{w}.annotation"))
            annotation = None
        rules.append(make_rule(d.get("id"), r, when, then, inputs, outputs, w, problems, annotation))
    return finish(Table(tid, name, version, policy, agg, collect_values, inputs, outputs, rules, "json", problems))


# ---------------------------------------------------------------- shared builders (JSON and XML)


def make_input(iid, label, expr, raw_type, values_text, w, problems) -> Input:
    if not isinstance(expr, str) or not expr.strip():
        problems.append(DmnError("INVALID_TABLE", "expression 必填：输入对象中的字段路径，如 applicant.age", f"{w}.expression"))
        expr = iid
    expr = expr.strip()
    path = expr.split(".")
    if not all(_PATH_PART.match(p.strip()) for p in path):
        problems.append(
            DmnError("UNSUPPORTED_EXPRESSION", f"输入表达式 {expr!r} 只能是字段路径（a.b.c），不支持 FEEL 运算", f"{w}.expression")
        )
    path = [p.strip() for p in path]
    t = normalize_type(raw_type)
    if t is None:
        problems.append(DmnError("INVALID_TABLE", f"未知类型 {raw_type!r}，可用：number string boolean date any", f"{w}.type"))
        t = "any"
    allowed = None
    if values_text is not None:
        if not isinstance(values_text, str):
            problems.append(DmnError("INVALID_TABLE", 'values 必须是 S-FEEL 一元测试字符串，如 "A","B" 或 [0..100]', f"{w}.values"))
            values_text = None
        else:
            try:
                allowed = parse_unary_tests(values_text)
            except DmnError as e:
                problems.append(DmnError(e.code, e.message, f"{w}.values"))
                values_text = None
    if label is not None and not isinstance(label, str):
        label = None
    inp = Input(iid, label or iid, expr, path, t, values_text, allowed)
    inp.universe = column_universe(t, allowed)
    return inp


def make_output(name, raw_type, values, has_default, default, w, problems) -> Output:
    if not isinstance(name, str) or not _NAME.match(name):
        problems.append(DmnError("INVALID_TABLE", "输出列 name 必须是 1–128 字符的字符串", f"{w}.name"))
        name = "result"
    t = normalize_type(raw_type)
    if t is None:
        problems.append(DmnError("INVALID_TABLE", f"未知类型 {raw_type!r}", f"{w}.type"))
        t = "any"
    vals = None
    if values is not None:
        vals = []
        for j, v in enumerate(values):
            try:
                fv = output_value(v, t)
            except ValueError as exc:
                problems.append(DmnError("TYPE_MISMATCH", f"{v!r} {exc}", f"{w}.values[{j}]"))
                continue
            if fv is None or isinstance(fv, (list, dict)):
                problems.append(DmnError("INVALID_TABLE", "values 中不能有 null、列表或对象", f"{w}.values[{j}]"))
                continue
            if any(same_output(fv, x) for x in vals):
                problems.append(DmnError("INVALID_TABLE", f"values 中 {v!r} 重复", f"{w}.values[{j}]"))
                continue
            vals.append(fv)
    dv = None
    if has_default:
        try:
            dv = output_value(default, t)
        except ValueError as exc:
            problems.append(DmnError("TYPE_MISMATCH", f"默认输出 {default!r} {exc}", f"{w}.default"))
        if vals and dv is not None and not any(same_output(dv, x) for x in vals):
            problems.append(DmnError("NOT_ALLOWED", f"默认输出 {default!r} 不在 values 中", f"{w}.default"))
    return Output(name, t, vals, has_default, dv)


def make_rule(rid, index, when, then, inputs, outputs, w, problems, annotation=None) -> Rule:
    if rid is None:
        rid = f"r{index + 1}"
    if not isinstance(rid, str) or not _NAME.match(rid):
        problems.append(DmnError("INVALID_TABLE", "规则 id 必须是 1–128 字符的字符串", f"{w}.id"))
        rid = f"r{index + 1}"
    tests = []
    for c, cell in enumerate(when):
        where = f"{w}.when[{c}]"
        try:
            t = parse_unary_tests(cell)
        except DmnError as e:
            problems.append(DmnError(e.code, f"规则 {rid} 第 {c + 1} 个条件 {cell!r}：{e.message}", where))
            t = AnyTest()
            t.text = cell if isinstance(cell, str) else "-"
            t.invalid = True
        tests.append(t)
        if c < len(inputs):
            check_entry_types(t, inputs[c], rid, c, where, problems)
    outs = []
    for c, v in enumerate(then):
        where = f"{w}.then[{c}]"
        o = outputs[c] if c < len(outputs) else None
        if o is None:
            outs.append(None)
            continue
        try:
            fv = output_value(v, o.type)
        except ValueError as exc:
            problems.append(DmnError("TYPE_MISMATCH", f"规则 {rid} 输出 {o.name} = {v!r} {exc}", where))
            fv = None
        if o.values and fv is not None and not any(same_output(fv, x) for x in o.values):
            problems.append(DmnError("NOT_ALLOWED", f"规则 {rid} 输出 {o.name} = {v!r} 不在 values 中", where))
        outs.append(fv)
    return Rule(rid, index, tests, outs, annotation)


def check_entry_types(t: Test, inp: Input, rid, c, where, problems):
    if inp.type == "any":
        return
    for lit in t.literals():
        k = kind(lit)
        if k != inp.type:
            problems.append(
                DmnError(
                    "TYPE_MISMATCH",
                    f"规则 {rid} 第 {c + 1} 个条件 {t.text!r}：{inp.label} 的类型是 {inp.type}，条件里却是 {k}",
                    where,
                )
            )
            return
    if inp.allowed is not None:
        # Equality literals must be allowed values; whether a range or a
        # negation can still match inside the allowed values is a check finding.
        for lit in _eq_literals(t):
            if inp.allowed.evaluate(lit) is not True:
                problems.append(
                    DmnError(
                        "NOT_ALLOWED",
                        f"规则 {rid} 第 {c + 1} 个条件 {t.text!r}：{describe(lit)} 不在 {inp.label} 的允许值 {inp.values_text} 中",
                        where,
                    )
                )
                return


def _eq_literals(t: Test) -> list:
    if isinstance(t, EqTest):
        return [t.value]
    if isinstance(t, AnyOf):
        return [v for x in t.items for v in _eq_literals(x)]
    if isinstance(t, NotTest):
        return _eq_literals(t.inner)
    return []


def finish(table: Table) -> Table:
    """Cross-field checks that depend on the hit policy."""
    p = table.problems
    ids = {}
    for r in table.rules:
        if r.id in ids:
            p.append(DmnError("INVALID_TABLE", f"规则 id {r.id} 重复（第 {ids[r.id] + 1} 与第 {r.index + 1} 条）", f"rules[{r.index}].id"))
        else:
            ids[r.id] = r.index
    seen = set()
    for i, inp in enumerate(table.inputs):
        if inp.id in seen:
            p.append(DmnError("INVALID_TABLE", f"输入列 id {inp.id} 重复", f"inputs[{i}].id"))
        seen.add(inp.id)
    seen = set()
    for i, o in enumerate(table.outputs):
        if o.name in seen:
            p.append(DmnError("INVALID_TABLE", f"输出列 {o.name} 重复", f"outputs[{i}].name"))
        seen.add(o.name)
    if table.hit_policy in ("PRIORITY", "OUTPUT ORDER") and not any(o.values for o in table.outputs):
        p.append(DmnError("INVALID_TABLE", f"{table.hit_policy} 需要至少一个输出列声明 values（按优先级从高到低）", "outputs"))
    if table.hit_policy in ("PRIORITY", "OUTPUT ORDER"):
        for r in table.rules:
            for c, o in enumerate(table.outputs):
                if o.values and r.outputs[c] is None:
                    p.append(
                        DmnError(
                            "INVALID_TABLE",
                            f"{table.hit_policy} 下规则 {r.id} 的 {o.name} 不能为空：优先级来自 values 列表",
                            f"rules[{r.index}].then[{c}]",
                        )
                    )
    if table.aggregation:
        if len(table.outputs) != 1:
            p.append(DmnError("INVALID_TABLE", f"COLLECT {table.aggregation} 只能有一个输出列", "outputs"))
        elif table.aggregation == "SUM" and table.outputs[0].type not in ("number", "any"):
            p.append(DmnError("INVALID_TABLE", "COLLECT SUM 的输出列必须是 number", "outputs[0].type"))
        elif table.aggregation in ("MIN", "MAX") and table.outputs[0].type == "boolean":
            p.append(DmnError("INVALID_TABLE", f"COLLECT {table.aggregation} 不能比较布尔值", "outputs[0].type"))
    return table
