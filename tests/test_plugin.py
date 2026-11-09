"""The real Dify SDK path: registration loads the provider and tools from the
YAML, and the tool classes yield the messages a workflow receives."""

import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
XML = (ROOT / "examples" / "loan-approval.dmn").read_text("utf-8")


@pytest.fixture(scope="module")
def registration():
    from dify_plugin import DifyPluginEnv
    from dify_plugin.core.plugin_registration import PluginRegistration

    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        yield PluginRegistration(DifyPluginEnv())
    finally:
        os.chdir(cwd)


def run(registration, tool, params):
    cls = registration.get_tool_cls("dmn", tool)
    assert cls is not None, tool
    inst = cls.from_credentials({})  # the SDK's own constructor path; no credentials needed
    out = {"text": [], "json": [], "vars": {}}
    for m in inst._invoke(params):
        kind = m.type.value if hasattr(m.type, "value") else str(m.type)
        if kind == "text":
            out["text"].append(m.message.text)
        elif kind == "json":
            out["json"].append(m.message.json_object)
        elif kind == "variable":
            out["vars"][m.message.variable_name] = m.message.variable_value
    return out


def test_registration_lists_both_tools(registration):
    names = [t.identity.name for p in registration.tools_configuration for t in p.tools]
    assert names == ["evaluate_decision_table", "check_decision_table"]
    assert registration.configuration.name == "dmn" and registration.configuration.author == "binhuhu"


def test_evaluate_tool_messages(registration):
    out = run(
        registration,
        "evaluate_decision_table",
        {
            "table": XML,
            "decision_id": "eligibility",
            "trace": False,
            "inputs": json.dumps({"applicant": {"creditScore": 750, "dti": 0.3, "employment": "EMPLOYED"}}),
        },
    )
    assert out["vars"]["status"] == "MATCHED"
    assert out["vars"]["outputs"] == {"decision": "APPROVE", "reason": "prime applicant"}
    assert out["vars"]["matched_rules"] == ["prime"]
    assert out["vars"]["decision"]["trace"] is None
    assert out["json"][0]["schema"] == "dmn-plugin/evaluation.v1"
    assert out["text"][0].startswith("MATCHED · 规则 prime")


def test_evaluate_tool_error_still_yields_variables(registration):
    out = run(registration, "evaluate_decision_table", {"table": "{", "inputs": {}})
    assert out["vars"]["status"] == "ERROR" and out["vars"]["outputs"] == {}
    assert out["text"][0].startswith("ERROR INVALID_JSON")


def test_check_tool_messages(registration):
    out = run(registration, "check_decision_table", {"table": XML, "decision_id": "fees"})
    assert out["vars"]["valid"] is True
    assert out["vars"]["report"]["decision"]["hit_policy"] == "COLLECT"
    assert "sha256 " in out["text"][0]
