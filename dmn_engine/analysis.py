"""Static checks for one decision table (no inputs needed).

Findings are ``{severity, code, rules, message, where?}``; severity is
``error`` (evaluation would refuse or the hit policy can be violated),
``warning`` (almost certainly a mistake) or ``info``.

Every finding is provable from the table: each cell denotes a set of values
(``sets.ValueSet``), so

- OVERLAP (UNIQUE: error)       some input matches two rules
- CONFLICT (ANY: error)         overlapping rules with different outputs
- SHADOWED (FIRST: warning)     every input of a rule already hits one earlier rule
- NEVER_MATCHES (warning)       a cell admits no value of its column
- GAP (info, one-input tables)  input values no rule covers
- NO_FALLBACK (info)            no catch-all rule and no default output
- DEFAULT_IGNORED (warning)     default outputs on a multi-hit table
"""

from __future__ import annotations

from .canonical import table_sha256
from .errors import DmnError
from .model import Table, load_table
from .sfeel import AnyTest
from .values import same_output

MAX_PAIRWISE_RULES = 400
MAX_FINDINGS = 200


def _rule_sets(table: Table):
    out = []
    for r in table.rules:
        out.append([t.value_set(inp.universe) for t, inp in zip(r.tests, table.inputs)])
    return out


def check_table(table: Table, dropped: dict | None = None) -> list[dict]:
    """Findings, capped at MAX_FINDINGS; findings beyond the cap are counted in ``dropped``."""
    findings: list[dict] = []
    if dropped is None:
        dropped = {}
    for sev in ("error", "warning", "info"):
        dropped.setdefault(sev, 0)

    def add(severity, code, rules, message, where=None):
        if len(findings) >= MAX_FINDINGS:
            dropped[severity] += 1  # still counted, so the totals stay true
            return
        f = {"severity": severity, "code": code, "rules": rules, "message": message}
        if where:
            f["where"] = where
        findings.append(f)

    for p in table.problems:
        add("error", p.code, [], p.message, p.where)
    if table.problems:
        return findings  # set reasoning on a broken table would mislead

    sets = _rule_sets(table)
    alive = []
    for r, cols in zip(table.rules, sets):
        dead = [c for c, s in enumerate(cols) if s.is_empty()]
        if dead:
            c = dead[0]
            add(
                "warning",
                "NEVER_MATCHES",
                [r.id],
                f"规则 {r.id} 的条件 {r.tests[c].text!r} 在 {table.inputs[c].label} 的取值范围内不可能成立，这条规则永远不会命中",
                f"rules[{r.index}].when[{c}]",
            )
        alive.append(not dead)

    policy = table.hit_policy
    n = len(table.rules)
    if n > MAX_PAIRWISE_RULES:
        add("info", "PAIRWISE_SKIPPED", [], f"规则超过 {MAX_PAIRWISE_RULES} 条，跳过两两重叠检查")
    else:
        for j in range(n):
            if not alive[j]:
                continue
            rj = table.rules[j]
            for i in range(j):
                if not alive[i]:
                    continue
                ri = table.rules[i]
                if not all(a.intersects(b) for a, b in zip(sets[i], sets[j])):
                    continue
                if policy == "UNIQUE":
                    witness = "；".join(
                        f"{inp.label} ∈ {sets[i][c].intersect(sets[j][c]).describe()}"
                        for c, inp in enumerate(table.inputs)
                        if not (isinstance(ri.tests[c], AnyTest) and isinstance(rj.tests[c], AnyTest))
                    )
                    add(
                        "error",
                        "OVERLAP",
                        [ri.id, rj.id],
                        f"UNIQUE 表中规则 {ri.id} 与 {rj.id} 可以同时命中"
                        + (f"（例如 {witness}）" if witness else "（两条规则都没有限制条件）"),
                    )
                elif policy == "ANY" and not all(same_output(a, b) for a, b in zip(ri.outputs, rj.outputs)):
                    add("error", "CONFLICT", [ri.id, rj.id], f"ANY 表中规则 {ri.id} 与 {rj.id} 可以同时命中，但输出不同")
                if policy == "FIRST" and all(b.subset_of(a) for a, b in zip(sets[i], sets[j])):
                    add(
                        "warning",
                        "SHADOWED",
                        [rj.id, ri.id],
                        f"FIRST 表中规则 {rj.id} 的每种命中情形都会先命中更早的规则 {ri.id}，它永远不会被选中",
                    )
                    break

    catch_all = any(all(isinstance(t, AnyTest) for t in r.tests) for r in table.rules)
    has_default = any(o.has_default for o in table.outputs)
    if not table.single_hit and has_default:
        add("warning", "DEFAULT_IGNORED", [], f"默认输出只用于单命中策略；{policy} 表无命中时返回空列表（或聚合的空值），默认值不会生效")
    if table.single_hit and not catch_all and not has_default:
        add("info", "NO_FALLBACK", [], "没有全为“-”的兜底规则，也没有默认输出；不满足任何规则时结果为 null（NO_MATCH）")
    if len(table.inputs) == 1 and table.rules:
        covered = None
        for cols, ok in zip(sets, alive):
            if ok:
                covered = cols[0] if covered is None else covered.union(cols[0])
        universe = table.inputs[0].universe
        gap = universe.minus(covered) if covered is not None else universe
        gap.null = False
        if not gap.is_empty() and not has_default:
            add("info", "GAP", [], f"{table.inputs[0].label} 取 {gap.describe()} 时没有规则命中")
    return findings


def check(raw, decision_id: str | None = None) -> dict:
    """Never raises; returns a report envelope."""
    report = {
        "schema": "dmn-plugin/check.v1",
        "valid": False,
        "decision": None,
        "counts": {"errors": 0, "warnings": 0, "infos": 0},
        "findings": [],
        "truncated": 0,
        "table": None,
    }
    try:
        return _check(raw, decision_id, report)
    except Exception as e:  # noqa: BLE001 - last resort, see evaluate()
        report["valid"] = False
        report["findings"] = [
            {
                "severity": "error",
                "code": "INTERNAL_ERROR",
                "rules": [],
                "message": f"检查器内部错误（{type(e).__name__}），请把决策表报告给插件维护者",
            }
        ]
        report["counts"] = {"errors": 1, "warnings": 0, "infos": 0}
        return report


def _check(raw, decision_id, report) -> dict:
    try:
        table = load_table(raw, decision_id)
    except DmnError as e:
        report["findings"] = [
            {"severity": "error", "code": e.code, "rules": [], "message": e.message, **({"where": e.where} if e.where else {})}
        ]
        report["counts"]["errors"] = 1
        return report
    normalized = table.normalized()
    report["decision"] = {
        "id": table.id,
        "name": table.name,
        "version": table.version,
        "hit_policy": table.hit_policy,
        "aggregation": table.aggregation,
        "inputs": len(table.inputs),
        "outputs": len(table.outputs),
        "rules": len(table.rules),
        "sha256": table_sha256(normalized),
        "source": table.source,
    }
    dropped: dict = {}
    findings = check_table(table, dropped)
    report["findings"] = findings
    report["truncated"] = sum(dropped.values())
    for sev, n in dropped.items():
        report["counts"][{"error": "errors", "warning": "warnings", "info": "infos"}[sev]] += n
    for f in findings:
        report["counts"][{"error": "errors", "warning": "warnings", "info": "infos"}[f["severity"]]] += 1
    report["valid"] = not table.problems and report["counts"]["errors"] == 0
    report["table"] = normalized
    return report
