"""Write dist/dmn-live-test.yml: a Dify workflow (DSL) that exercises both
tools on the examples, for a manual check after installing a build.

    python scripts/live_test_dsl.py

Import it in Dify (Studio, Create, Import app). It creates a separate draft
app; run it with the default inputs and expect fee 28 (rules base, heavy,
domestic, express), loan APPROVE (rule prime), check valid. The third node
takes its inputs as a JSON constant: fee 20 (rules base, remote).
"""

import json
from pathlib import Path

import yaml

PLUGIN = Path(__file__).resolve().parent.parent
OUT = PLUGIN / "dist" / "dmn-live-test.yml"

fee_table = json.dumps(json.loads((PLUGIN / "examples/shipping-fee.json").read_text("utf-8")), ensure_ascii=False, separators=(",", ":"))
loan_xml = (PLUGIN / "examples/loan-approval.dmn").read_text("utf-8")
ev_yaml = yaml.safe_load((PLUGIN / "tools/evaluate_decision_table.yaml").read_text("utf-8"))
ck_yaml = yaml.safe_load((PLUGIN / "tools/check_decision_table.yaml").read_text("utf-8"))
VERSION = yaml.safe_load((PLUGIN / "manifest.yaml").read_text("utf-8"))["version"]


def const(v):
    return {"type": "constant", "value": v}


def tool_node(node_id, x, title, tool, configs, params):
    spec = ev_yaml if tool == "evaluate_decision_table" else ck_yaml
    return {
        "data": {
            "desc": "",
            "output_schema": spec["output_schema"],
            "plugin_id": "binhuhu/dmn",
            "provider_id": "binhuhu/dmn/dmn",
            "provider_name": "binhuhu/dmn/dmn",
            "provider_type": "builtin",
            "title": title,
            "tool_configurations": configs,
            "tool_label": spec["identity"]["label"]["zh_Hans"],
            "tool_name": tool,
            "tool_node_version": "2",
            "tool_parameters": params,
            "type": "tool",
            "version": "2",
        },
        "height": 90,
        "id": node_id,
        "position": {"x": x, "y": 220},
        "positionAbsolute": {"x": x, "y": 220},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 244,
    }


def plain(node_id, x, data):
    return {
        "data": data,
        "height": 90,
        "id": node_id,
        "position": {"x": x, "y": 220},
        "positionAbsolute": {"x": x, "y": 220},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": 244,
    }


start = plain(
    "start",
    0,
    {
        "desc": "",
        "title": "开始",
        "type": "start",
        "variables": [
            {"variable": "weight", "label": "重量 kg", "type": "number", "required": True, "default": 12},
            {
                "variable": "region",
                "label": "地区",
                "type": "select",
                "required": True,
                "default": "DOMESTIC",
                "options": ["LOCAL", "DOMESTIC", "REMOTE"],
            },
            {"variable": "express", "label": "加急", "type": "select", "required": True, "default": "true", "options": ["true", "false"]},
            {"variable": "credit_score", "label": "信用分", "type": "number", "required": True, "default": 750},
            {"variable": "dti", "label": "负债收入比", "type": "number", "required": True, "default": 0.3},
            {
                "variable": "employment",
                "label": "就业",
                "type": "select",
                "required": True,
                "default": "EMPLOYED",
                "options": ["EMPLOYED", "SELF_EMPLOYED", "UNEMPLOYED"],
            },
        ],
    },
)

code = plain(
    "facts",
    300,
    {
        "desc": "",
        "title": "组装输入对象",
        "type": "code",
        "code_language": "python3",
        "code": (
            "def main(weight, region: str, express: str, credit_score, dti, employment: str) -> dict:\n"
            "    return {\n"
            '        "parcel_input": {"parcel": {"weight": weight, "region": region, "express": express == "true"}},\n'
            '        "loan_input": {"applicant": {"creditScore": credit_score, "dti": dti, "employment": employment}},\n'
            "    }\n"
        ),
        "variables": [
            {"variable": v, "value_selector": ["start", v]} for v in ("weight", "region", "express", "credit_score", "dti", "employment")
        ],
        "outputs": {"parcel_input": {"type": "object", "children": None}, "loan_input": {"type": "object", "children": None}},
    },
)

fee = tool_node(
    "fee",
    600,
    "运费 JSON 表（COLLECT SUM）",
    "evaluate_decision_table",
    {"table": const(fee_table), "decision_id": const(""), "expected_sha256": const(""), "trace": const(True)},
    {"inputs": {"type": "variable", "value": ["facts", "parcel_input"]}},
)
fee_text = tool_node(
    "fee_text",
    900,
    "运费 · 输入为 JSON 常量",
    "evaluate_decision_table",
    {"table": const(fee_table), "decision_id": const(""), "expected_sha256": const(""), "trace": const(False)},
    {"inputs": const(json.dumps({"parcel": {"weight": 3, "region": "REMOTE", "express": True}}))},
)
loan = tool_node(
    "loan",
    1200,
    "贷款 DMN XML（PRIORITY）",
    "evaluate_decision_table",
    {"table": const(loan_xml), "decision_id": const("eligibility"), "expected_sha256": const(""), "trace": const(True)},
    {"inputs": {"type": "variable", "value": ["facts", "loan_input"]}},
)
chk = tool_node("check", 1500, "检查贷款表", "check_decision_table", {"table": const(loan_xml), "decision_id": const("eligibility")}, {})

end = plain(
    "end",
    1800,
    {
        "desc": "",
        "title": "结束",
        "type": "end",
        "outputs": [
            {"variable": "fee_status", "value_selector": ["fee", "status"], "value_type": "string"},
            {"variable": "fee_outputs", "value_selector": ["fee", "outputs"], "value_type": "object"},
            {"variable": "fee_rules", "value_selector": ["fee", "matched_rules"], "value_type": "array[string]"},
            {"variable": "fee_text_value", "value_selector": ["fee_text", "value"], "value_type": "string"},
            {"variable": "loan_status", "value_selector": ["loan", "status"], "value_type": "string"},
            {"variable": "loan_value", "value_selector": ["loan", "value"], "value_type": "string"},
            {"variable": "loan_outputs", "value_selector": ["loan", "outputs"], "value_type": "object"},
            {"variable": "loan_decision", "value_selector": ["loan", "decision"], "value_type": "object"},
            {"variable": "check_valid", "value_selector": ["check", "valid"], "value_type": "boolean"},
            {"variable": "check_sha256", "value_selector": ["check", "sha256"], "value_type": "string"},
            {"variable": "check_report", "value_selector": ["check", "report"], "value_type": "object"},
        ],
    },
)

order = [start, code, fee, fee_text, loan, chk, end]
edges = []
for a, b in zip(order, order[1:]):
    edges.append(
        {
            "data": {"isInIteration": False, "isInLoop": False, "sourceType": a["data"]["type"], "targetType": b["data"]["type"]},
            "id": f"{a['id']}-source-{b['id']}-target",
            "source": a["id"],
            "sourceHandle": "source",
            "target": b["id"],
            "targetHandle": "target",
            "type": "custom",
        }
    )

dsl = {
    "app": {
        "description": f"binhuhu/dmn {VERSION} 插件实测（草稿，不发布）",
        "icon": "🧪",
        "icon_background": "#E4FBCC",
        "icon_type": "emoji",
        "mode": "workflow",
        "name": f"DMN 插件 {VERSION} 实测",
        "use_icon_as_answer_icon": False,
    },
    "dependencies": [],
    "kind": "app",
    "version": "0.7.0",
    "workflow": {
        "conversation_variables": [],
        "environment_variables": [],
        "features": {
            "file_upload": {"enabled": False},
            "opening_statement": "",
            "retriever_resource": {"enabled": False},
            "sensitive_word_avoidance": {"enabled": False},
            "speech_to_text": {"enabled": False},
            "suggested_questions": [],
            "suggested_questions_after_answer": {"enabled": False},
            "text_to_speech": {"enabled": False, "language": "", "voice": ""},
        },
        "graph": {"edges": edges, "nodes": order, "viewport": {"x": 40, "y": 100, "zoom": 0.6}},
        "rag_pipeline_variables": [],
    },
}
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False, width=100000), "utf-8")
print(OUT, OUT.stat().st_size)
