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


def test_value_is_first_output_as_text(registration):
    applicant = {"applicant": {"creditScore": 750, "dti": 0.3, "employment": "EMPLOYED"}}
    out = run(registration, "evaluate_decision_table", {"table": XML, "decision_id": "eligibility", "inputs": applicant})
    assert out["vars"]["value"] == "APPROVE"
    fee = (ROOT / "examples" / "shipping-fee.json").read_text("utf-8")
    parcel = {"parcel": {"weight": 12, "region": "DOMESTIC", "express": True}}
    assert run(registration, "evaluate_decision_table", {"table": fee, "inputs": parcel})["vars"]["value"] == "28"
    rule_order = {
        "format": "dmn-table/v1",
        "id": "tags",
        "hit_policy": "RULE ORDER",
        "inputs": [{"id": "n", "expression": "n", "type": "number"}],
        "outputs": [{"name": "tag", "type": "string"}],
        "rules": [{"id": "a", "when": ["> 1"], "then": ["big"]}, {"id": "b", "when": ["-"], "then": ["any"]}],
    }
    out = run(registration, "evaluate_decision_table", {"table": json.dumps(rule_order), "inputs": {"n": 5}})
    assert out["vars"]["value"] == '["big","any"]'
    assert run(registration, "evaluate_decision_table", {"table": "{", "inputs": {}})["vars"]["value"] == ""


def test_check_tool_exposes_sha256(registration):
    out = run(registration, "check_decision_table", {"table": XML, "decision_id": "fees"})
    assert out["vars"]["sha256"] == out["vars"]["report"]["decision"]["sha256"]
    assert len(out["vars"]["sha256"]) == 64
    assert run(registration, "check_decision_table", {"table": "{"})["vars"]["sha256"] == ""


# --- How Dify's workflow editor reads the tool YAML ----------------------------
# Found in a live test of 0.1.0 on cloud.dify.ai: the editor draws no input box
# for `type: any` parameters, and it labels an object output "File" whenever the
# output's schema has no typed key that Dify's built-in File schema lacks.

import yaml  # noqa: E402

TOOL_YAML = [ROOT / "tools" / "evaluate_decision_table.yaml", ROOT / "tools" / "check_decision_table.yaml"]
# Parameter types the editor's form renderer has a control for.
RENDERED_TYPES = {
    "string", "number", "boolean", "select", "secret-input", "file", "files", "object", "array",
    "checkbox", "dynamic-select", "date", "date-range", "model-selector", "app-selector",
}  # fmt: skip
# Shape of Dify's built-in File schema (api/core/schemas/builtin/schemas/v1/file.json).
DIFY_FILE_SCHEMA = {
    "type": "object",
    "properties": {
        k: {"type": t}
        for k, t in [("name", "string"), ("size", "number"), ("extension", "string"), ("type", "string"),
                     ("mime_type", "string"), ("transfer_method", "string"), ("url", "string"), ("related_id", "string")]
    },
    "required": ["name"],
}  # fmt: skip


def dify_schema_matches(schema, target) -> bool:
    """Port of Dify's web matchTheSchemaType (nodes/_base/components/variable/
    match-schema-type.ts): only `type` values are compared, and a key missing on
    one side only fails when the other side has a `type` beneath it."""

    def is_obj(x):
        return isinstance(x, (dict, list))

    def keys(x):
        return list(x) if isinstance(x, dict) else [str(i) for i in range(len(x))]

    def get(x, k):
        if isinstance(x, dict):
            return x.get(k)
        if isinstance(x, list) and k.isdigit() and int(k) < len(x):
            return x[int(k)]
        return None

    def is_match(s, t):
        if not is_obj(s):
            return True
        if not is_obj(t):
            for k in keys(s):
                if k == "type":
                    return False
                v = get(s, k)
                if is_obj(v) and not is_match(v, None):
                    return False
            return True
        tx, ty = get(s, "type"), get(t, "type")
        if not is_obj(tx) and tx != ty:
            return False
        for k in set(keys(s)) | set(keys(t)):
            if k == "type" and not is_obj(tx):
                continue
            if not is_match(get(s, k), get(t, k)):
                return False
        return True

    return is_match(schema, target)


def test_matcher_port_reproduces_the_file_mislabel():
    assert dify_schema_matches({"type": "object", "description": "x"}, DIFY_FILE_SCHEMA)
    assert not dify_schema_matches({"type": "object", "properties": {"a": {"type": "string"}}}, DIFY_FILE_SCHEMA)


@pytest.mark.parametrize("path", TOOL_YAML, ids=lambda p: p.stem)
def test_every_parameter_has_an_editor_control(path):
    spec = yaml.safe_load(path.read_text("utf-8"))
    for p in spec["parameters"]:
        assert p["type"] in RENDERED_TYPES, f"{p['name']}: Dify draws no control for type {p['type']!r}"


@pytest.mark.parametrize("path", TOOL_YAML, ids=lambda p: p.stem)
def test_object_outputs_are_not_mistaken_for_files(path):
    spec = yaml.safe_load(path.read_text("utf-8"))
    for name, prop in spec["output_schema"]["properties"].items():
        if prop.get("type") == "object":
            assert not dify_schema_matches(prop, DIFY_FILE_SCHEMA), f"{name} would show as File"
            assert not dify_schema_matches(prop, {"type": "object"}), f"{name} has no typed key"


def test_declared_outputs_are_the_variables_yielded(registration):
    applicant = {"applicant": {"creditScore": 750, "dti": 0.3, "employment": "EMPLOYED"}}
    ev = run(registration, "evaluate_decision_table", {"table": XML, "decision_id": "eligibility", "inputs": applicant})
    ck = run(registration, "check_decision_table", {"table": XML, "decision_id": "fees"})
    for path, out in zip(TOOL_YAML, (ev, ck)):
        declared = set(yaml.safe_load(path.read_text("utf-8"))["output_schema"]["properties"])
        assert declared == set(out["vars"]), path.stem
