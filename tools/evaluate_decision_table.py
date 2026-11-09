import json

from dify_plugin import Tool

from dmn_engine import evaluate


def summary(env: dict) -> str:
    status = env["status"]
    if status == "ERROR":
        err = env.get("error") or {}
        return f"ERROR {err.get('code')}: {err.get('message')}"
    rules = "、".join(env["selected_rules"]) or "—"
    outputs = json.dumps(env["outputs"], ensure_ascii=False)
    extra = []
    if env["missing_inputs"]:
        extra.append("缺少输入 " + "、".join(env["missing_inputs"]))
    if env["type_mismatches"]:
        extra.append("类型不符 " + "、".join(m["input"] for m in env["type_mismatches"]))
    return f"{status} · 规则 {rules} · {outputs}" + (" · " + "；".join(extra) if extra else "")


class EvaluateDecisionTableTool(Tool):
    def _invoke(self, tool_parameters):
        trace = tool_parameters.get("trace")
        env = evaluate(
            tool_parameters.get("table"),
            tool_parameters.get("inputs"),
            decision_id=(tool_parameters.get("decision_id") or None),
            expected_sha256=(tool_parameters.get("expected_sha256") or None),
            trace=True if trace is None else bool(trace),
        )
        yield self.create_text_message(summary(env))
        yield self.create_json_message(env)
        yield self.create_variable_message("status", env["status"])
        yield self.create_variable_message("outputs", env["outputs"] or {})
        yield self.create_variable_message("matched_rules", env["matched_rules"])
        yield self.create_variable_message("decision", env)
