import copy
import json
from pathlib import Path

import pytest

from dmn_engine import evaluate

SUITE = json.loads((Path(__file__).parent / "conformance" / "hit-policies.json").read_text("utf-8"))


def build(case):
    table = copy.deepcopy(SUITE["tables"][case["table"]])
    if "hit_policy" in case:
        table["hit_policy"] = case["hit_policy"]
    table.update(copy.deepcopy(case.get("patch", {})))
    return table


@pytest.mark.parametrize("case", SUITE["cases"], ids=[c["name"] for c in SUITE["cases"]])
def test_conformance(case):
    env = evaluate(build(case), case["inputs"])
    assert env["status"] == case["status"], env["error"]
    if "result" in case:
        assert env["result"] == case["result"]
    if "selected" in case:
        assert env["selected_rules"] == case["selected"]
    if "error" in case:
        assert env["error"]["code"] == case["error"]


def test_every_table_in_the_suite_is_clean():
    from dmn_engine import load_table

    for name, t in SUITE["tables"].items():
        assert load_table(t).problems == [], name
