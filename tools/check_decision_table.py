from dify_plugin import Tool

from dmn_engine.analysis import check


def summary(report: dict) -> str:
    c = report["counts"]
    d = report.get("decision") or {}
    head = f"{'通过' if report['valid'] else '未通过'} · {d.get('id', '—')} · {d.get('hit_policy', '—')} · 错误 {c['errors']} · 警告 {c['warnings']} · 提示 {c['infos']}"
    lines = [head]
    if d.get("sha256"):
        lines.append(f"sha256 {d['sha256']}")
    for f in report["findings"][:20]:
        lines.append(f"- [{f['severity']}] {f['code']}: {f['message']}")
    if len(report["findings"]) > 20:
        lines.append(f"…另有 {len(report['findings']) - 20} 条")
    return "\n".join(lines)


class CheckDecisionTableTool(Tool):
    def _invoke(self, tool_parameters):
        report = check(tool_parameters.get("table"), tool_parameters.get("decision_id") or None)
        yield self.create_text_message(summary(report))
        yield self.create_json_message(report)
        yield self.create_variable_message("valid", report["valid"])
        yield self.create_variable_message("sha256", (report.get("decision") or {}).get("sha256") or "")
        yield self.create_variable_message("report", report)
