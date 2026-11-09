"""Import one decision table from DMN 1.1–1.5 XML.

Reads ``definitions/decision/decisionTable`` (any DMN namespace, vendor
extensions ignored): ``hitPolicy`` / ``aggregation``, ``input`` with
``inputExpression`` (typeRef, text) and ``inputValues``, ``output`` with
``outputValues`` and ``defaultOutputEntry``, and ``rule`` with
``inputEntry`` / ``outputEntry`` / ``description`` / ``annotationEntry``.

Cells must be S-FEEL (see ``sfeel``); output entries must be literals.
Decision requirements (DRG chaining) are not evaluated: values that come from
required decisions must be passed in the inputs.

Safety: DOCTYPE and entity declarations are refused before parsing (no entity
expansion, no external resources); size is capped.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .errors import DmnError
from .model import MAX_TABLE_BYTES, Table, _hit_policy, finish, make_input, make_output, make_rule
from .sfeel import parse_literal, parse_literal_list
from .values import to_json

_FORBIDDEN = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _child(el, name):
    for c in el:
        if _local(c.tag) == name:
            return c
    return None


def _text_of(el):
    """Text of an element's <text> child (DMN unaryTests / literalExpression)."""
    if el is None:
        return None
    t = _child(el, "text")
    if t is None:
        return None
    return (t.text or "").strip()


def list_decisions(root) -> list[dict]:
    out = []
    for el in root.iter():
        if _local(el.tag) == "decision":
            dt = _child(el, "decisionTable")
            out.append({"id": el.get("id"), "name": el.get("name"), "has_table": dt is not None, "el": el, "table": dt})
    return out


def table_from_xml(text: str, decision_id: str | None = None) -> Table:
    if len(text.encode("utf-8")) > MAX_TABLE_BYTES:
        raise DmnError("TOO_LARGE", f"DMN XML 超过 {MAX_TABLE_BYTES // 1024} KiB")
    if _FORBIDDEN.search(text):
        raise DmnError("INVALID_XML", "DMN XML 不能包含 DOCTYPE 或实体声明")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DmnError("INVALID_XML", f"DMN XML 无法解析：{exc}") from exc
    if _local(root.tag) != "definitions":
        raise DmnError("INVALID_XML", "根元素必须是 DMN <definitions>")
    decisions = [d for d in list_decisions(root) if d["has_table"]]
    if not decisions:
        raise DmnError("NO_DECISION_TABLE", "文件中没有带决策表（decisionTable）的 decision")
    if decision_id:
        picked = [d for d in decisions if decision_id in (d["id"], d["name"])]
        if not picked:
            ids = "、".join(str(d["id"]) for d in decisions)
            raise DmnError("DECISION_NOT_FOUND", f"没有 id 或 name 为 {decision_id!r} 的决策表；可用：{ids}")
        d = picked[0]
    elif len(decisions) == 1:
        d = decisions[0]
    else:
        ids = "、".join(f"{x['id']}（{x['name']}）" for x in decisions)
        raise DmnError("AMBIGUOUS_DECISION", f"文件中有 {len(decisions)} 张决策表，请用 decision_id 指定：{ids}")

    dt = d["table"]
    problems: list[DmnError] = []
    policy, agg = _hit_policy(dt.get("hitPolicy") or "UNIQUE", dt.get("aggregation"), problems)

    inputs = []
    for i, el in enumerate(_children(dt, "input")):
        w = f"inputs[{i}]"
        ie = _child(el, "inputExpression")
        expr = _text_of(ie)
        type_ref = ie.get("typeRef") if ie is not None else None
        iid = el.get("id") or el.get("label") or expr or f"input{i + 1}"
        values = _text_of(_child(el, "inputValues"))
        inputs.append(make_input(iid, el.get("label"), expr, type_ref, values or None, w, problems))

    outputs = []
    out_els = _children(dt, "output")
    for i, el in enumerate(out_els):
        w = f"outputs[{i}]"
        name = (
            el.get("name")
            or el.get("label")
            or (dt.get("outputLabel") if len(out_els) == 1 else None)
            or (d["name"] if len(out_els) == 1 else None)
        )
        values = None
        vtext = _text_of(_child(el, "outputValues"))
        if vtext:
            try:
                values = to_json(parse_literal_list(vtext))
            except DmnError as e:
                problems.append(DmnError(e.code, f"outputValues {vtext!r}：{e.message}", f"{w}.values"))
        default_el = _child(el, "defaultOutputEntry")
        has_default, default = False, None
        if default_el is not None:
            dtext = _text_of(default_el)
            try:
                default, has_default = to_json(parse_literal(dtext or "")), True
            except DmnError as e:
                problems.append(DmnError(e.code, f"defaultOutputEntry {dtext!r}：{e.message}", f"{w}.default"))
        outputs.append(make_output(name, el.get("typeRef"), values, has_default, default, w, problems))
    if not outputs:
        raise DmnError("INVALID_TABLE", "决策表没有 output 列", "outputs")

    rules = []
    for r, el in enumerate(_children(dt, "rule")):
        w = f"rules[{r}]"
        when = [_text_of(e) or "-" for e in _children(el, "inputEntry")]
        then = []
        for c, e in enumerate(_children(el, "outputEntry")):
            t = _text_of(e)
            try:
                then.append(to_json(parse_literal(t or "")))
            except DmnError as err:
                problems.append(DmnError(err.code, f"规则 {el.get('id') or r + 1} 输出 {t!r}：{err.message}", f"{w}.then[{c}]"))
                then.append(None)
        if len(when) != len(inputs):
            problems.append(
                DmnError("INVALID_TABLE", f"规则 {el.get('id') or r + 1} 有 {len(when)} 个 inputEntry，应为 {len(inputs)}", f"{w}.when")
            )
            when = (when + ["-"] * len(inputs))[: len(inputs)]
        if len(then) != len(outputs):
            problems.append(
                DmnError("INVALID_TABLE", f"规则 {el.get('id') or r + 1} 有 {len(then)} 个 outputEntry，应为 {len(outputs)}", f"{w}.then")
            )
            then = (then + [None] * len(outputs))[: len(outputs)]
        desc = _child(el, "description")
        annotation = (desc.text or "").strip() if desc is not None and desc.text else None
        if annotation is None:
            ann = [_text_of(a) for a in _children(el, "annotationEntry")]
            annotation = " / ".join(a for a in ann if a) or None
        rules.append(make_rule(el.get("id"), r, when, then, inputs, outputs, w, problems, annotation))

    tid = d["id"] or d["name"] or "decision"
    table = Table(
        tid[:128], (d["name"] or None) and d["name"][:128], None, policy, agg, "distinct", inputs, outputs, rules, "dmn-xml", problems
    )
    return finish(table)
