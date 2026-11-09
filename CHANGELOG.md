# Changelog

## 0.1.0 — 2026-11-09

First release of the standalone DMN plugin (`binhuhu/dmn`), decoupled from `dify_dmn_plugin` / `hu8627/dmn_json`.

- Engine (standard library only): all seven DMN hit policies, COLLECT aggregations with spec "distinct" semantics by default and `collect_values: "all"` for Camunda 7 behaviour, decimal arithmetic, default outputs, output priorities across several columns.
- S-FEEL unary tests: literals, comparisons, open/closed ranges, lists, `not(...)`, `null`, `date("…")`; anything else is refused with `UNSUPPORTED_EXPRESSION`.
- Input formats: JSON `dmn-table/v1` and DMN 1.1–1.5 XML (DOCTYPE/entities refused).
- Tools: `evaluate_decision_table` (status, outputs, matched rules, full envelope with trace) and `check_decision_table` (overlaps, conflicts, shadowed / impossible rules, gaps, all load errors with locations; RFC 8785 sha256 pin).
- Tests: 110 (unit, a language-neutral conformance suite, the real SDK registration and tool invocation); `scripts/package.py --verify` runs both tools from the unpacked package in a fresh environment.
