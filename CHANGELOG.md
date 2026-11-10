# Changelog

## 0.1.1 — 2026-11-09

Fixes from the first live test on cloud.dify.ai (0.1.0 ran correctly, but the workflow editor could not edit it well).

- `table` is now a `string` parameter and `inputs` an `object` parameter. Dify's editor draws no input box for `type: any`, so 0.1.0 tables could only be set by importing a DSL. Now the table is a text box (paste JSON or XML, or insert a text variable) and the inputs take an object variable or a JSON constant. The engine still accepts both shapes, so 0.1.0 nodes keep working.
- Object outputs (`outputs`, `decision`, `report`) were labelled "File" in the editor: Dify's schema matcher treats an object with no typed keys as its built-in File schema. They now declare typed properties, which also makes fields such as `decision.status` and `decision.error.code` selectable downstream.
- New `value` output: the first output column as text, so an IF/ELSE node can branch on the decision without a Code node.
- New `sha256` output on the check tool.
- Tests: 119. New guards check every parameter type against the controls Dify's editor renders, and run a port of Dify's schema matcher over every object output.
- Licensed under Apache-2.0: `LICENSE` and `NOTICE` are in the repository and in the `.difypkg`; the package build fails if either is missing.
- `scripts/live_test_dsl.py` writes `dist/dmn-live-test.yml`, the workflow used for the live test: import it in Dify, run it, compare with the expected results in its docstring.

## 0.1.0 — 2026-11-09

First release of the standalone DMN plugin (`binhuhu/dmn`), decoupled from `dify_dmn_plugin` / `hu8627/dmn_json`.

- Engine (standard library only): all seven DMN hit policies, COLLECT aggregations with spec "distinct" semantics by default and `collect_values: "all"` for Camunda 7 behaviour, decimal arithmetic, default outputs, output priorities across several columns.
- S-FEEL unary tests: literals, comparisons, open/closed ranges, lists, `not(...)`, `null`, `date("…")`; anything else is refused with `UNSUPPORTED_EXPRESSION`.
- Input formats: JSON `dmn-table/v1` and DMN 1.1–1.5 XML (DOCTYPE/entities refused).
- Tools: `evaluate_decision_table` (status, outputs, matched rules, full envelope with trace) and `check_decision_table` (overlaps, conflicts, shadowed / impossible rules, gaps, all load errors with locations; RFC 8785 sha256 pin).
- Tests: 111 (unit, a language-neutral conformance suite, the real SDK registration and tool invocation); `scripts/package.py --verify` runs both tools from the unpacked package in a fresh environment.
