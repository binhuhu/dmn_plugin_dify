import json
from decimal import Decimal
from pathlib import Path

import pytest

from dmn_engine import DmnError, canonicalize, evaluate, load_table, table_sha256
from dmn_engine.analysis import check
from dmn_engine.sets import ValueSet
from dmn_engine.sfeel import parse_literal, parse_literal_list, parse_unary_tests

ROOT = Path(__file__).resolve().parent.parent
XML = (ROOT / "examples" / "loan-approval.dmn").read_text("utf-8")
D = Decimal


# ---------------------------------------------------------------- S-FEEL


@pytest.mark.parametrize(
    "cell,value,expected",
    [
        ("-", None, True),
        ("", "x", True),
        ("5", D(5), True),
        ("5", D("5.0"), True),
        ("5", "5", False),
        ('"a"', "a", True),
        ("true", True, True),
        ("true", False, False),
        ("false", None, False),
        ("< 5", D(4), True),
        ("< 5", D(5), False),
        ("<= 5", D(5), True),
        ("> -2", D(-1), True),
        (">= -2.5", D("-2.5"), True),
        ("[1..10]", D(1), True),
        ("[1..10]", D(10), True),
        ("]1..10]", D(1), False),
        ("(1..10)", D(10), False),
        ("[1..10[", D(10), False),
        ("< 5", None, None),
        ("< 5", "a", None),
        ("[1..10]", "a", None),
        ("1, 2, > 10", D(11), True),
        ("1, 2, > 10", D(3), False),
        ("1, > 10", None, None),
        ("not(1, 2)", D(3), True),
        ("not(1, 2)", D(2), False),
        ("not(1, 2)", None, True),
        ("not(< 5)", None, None),
        ("null", None, True),
        ("null", D(0), False),
        ("null, > 3", None, True),
        ('"a\\"b"', 'a"b', True),
        ('date("2026-01-01")', parse_literal('date("2026-01-01")'), True),
        ('["a".."c"]', "b", True),
        ('["a".."c"]', "d", False),
    ],
)
def test_unary_tests(cell, value, expected):
    assert parse_unary_tests(cell).evaluate(value) is expected


@pytest.mark.parametrize(
    "cell,code",
    [
        ("< x", "UNSUPPORTED_EXPRESSION"),
        ("!= 5", "UNSUPPORTED_EXPRESSION"),
        ("age > 5", "UNSUPPORTED_EXPRESSION"),
        ("[1..", "INVALID_ENTRY"),
        ('[1.."a"]', "INVALID_ENTRY"),
        ("< true", "INVALID_ENTRY"),
        ("1,,2", "INVALID_ENTRY"),
        ('date("2026-13-01")', "INVALID_ENTRY"),
        ("5 6", "INVALID_ENTRY"),
        ("@", "INVALID_ENTRY"),
        ("< null", "INVALID_ENTRY"),
        ("x" * 3000, "INVALID_ENTRY"),
    ],
)
def test_unary_test_errors(cell, code):
    with pytest.raises(DmnError) as e:
        parse_unary_tests(cell)
    assert e.value.code == code


def test_literals():
    assert parse_literal('"x"') == "x"
    assert parse_literal("-1.5") == D("-1.5")
    assert parse_literal("") is None and parse_literal("null") is None
    assert parse_literal_list('"A", "B",3') == ["A", "B", D(3)]


# ---------------------------------------------------------------- sets


def test_value_sets():
    u = ValueSet.universe(("number",))
    lt5 = parse_unary_tests("< 5").value_set(u)
    r = parse_unary_tests("[5..10]").value_set(u)
    assert not lt5.intersects(r)
    assert parse_unary_tests("<= 5").value_set(u).intersects(r)
    assert parse_unary_tests("[6..7]").value_set(u).subset_of(r)
    assert not parse_unary_tests("[6..11]").value_set(u).subset_of(r)
    assert parse_unary_tests("[10..1]").value_set(u).is_empty()
    gap = u.minus(lt5).minus(r)
    gap.null = False
    assert gap.describe() == "(10..+∞)"
    b = ValueSet.universe(("boolean",))
    rest = b.minus(parse_unary_tests("true").value_set(b)).minus(parse_unary_tests("false").value_set(b))
    rest.null = False
    assert rest.is_empty()
    not_list = parse_unary_tests('not("a", "b")').value_set(ValueSet.universe(("string",)))
    assert not_list.null and not not_list.intersects(parse_unary_tests('"a"').value_set(ValueSet.universe(("string",))))


# ---------------------------------------------------------------- model / evaluation


def table(**kw):
    t = {
        "format": "dmn-table/v1",
        "id": "t",
        "hit_policy": "UNIQUE",
        "inputs": [{"id": "x", "expression": "x", "type": "number"}],
        "outputs": [{"name": "o", "type": "string"}],
        "rules": [{"id": "r1", "when": ["< 5"], "then": ["low"]}],
    }
    t.update(kw)
    return t


def codes(t):
    return [p.code for p in load_table(t).problems]


def test_load_problems_are_collected_with_locations():
    t = table(
        rules=[
            {"id": "a", "when": ['< "z"'], "then": ["x"]},
            {"id": "a", "when": ["foo"], "then": [5]},
            {"id": "c", "when": [], "then": ["x"]},
        ],
        extra=1,
    )
    loaded = load_table(t)
    got = {(p.code, p.where) for p in loaded.problems}
    assert ("TYPE_MISMATCH", "rules[0].when[0]") in got
    assert ("UNSUPPORTED_EXPRESSION", "rules[1].when[0]") in got
    assert ("TYPE_MISMATCH", "rules[1].then[0]") in got
    assert ("INVALID_TABLE", "rules[2].when") in got
    assert ("INVALID_TABLE", "extra") in got
    assert any("重复" in p.message for p in loaded.problems)


def test_policy_specific_validation():
    assert "INVALID_TABLE" in codes(table(hit_policy="PRIORITY"))  # needs output values
    assert codes(table(hit_policy="C+", outputs=[{"name": "o", "type": "string"}])) == ["INVALID_TABLE"]
    assert codes(
        table(hit_policy="COLLECT", aggregation="SUM", outputs=[{"name": "a", "type": "number"}, {"name": "b", "type": "number"}], rules=[])
    ) == ["INVALID_TABLE"]
    assert codes(table(hit_policy="FIRST", aggregation="SUM")) == ["INVALID_TABLE"]
    assert codes(table(hit_policy="C#", aggregation="SUM")) == ["INVALID_TABLE"]
    assert codes(table(outputs=[{"name": "o", "type": "string", "values": ["a"]}])) == ["NOT_ALLOWED"]
    allowed = [{"id": "x", "expression": "x", "type": "string", "values": '"a","b"'}]
    assert codes(table(inputs=allowed, rules=[{"id": "r", "when": ['not("a")'], "then": ["x"]}])) == []
    assert codes(table(inputs=allowed, rules=[{"id": "r", "when": ['"c"'], "then": ["x"]}])) == ["NOT_ALLOWED"]


def test_hit_policy_aliases():
    for short, full in [("U", "UNIQUE"), ("rule_order", "RULE ORDER"), ("output order", "OUTPUT ORDER"), ("c#", "COLLECT")]:
        assert load_table(table(hit_policy=short, outputs=[{"name": "o", "type": "any", "values": ["low"]}])).hit_policy == full


def test_refusals_are_envelopes():
    env = evaluate("{not json", {})
    assert env["status"] == "ERROR" and env["error"]["code"] == "INVALID_JSON"
    env = evaluate(table(), "[1]")
    assert env["error"]["code"] == "INVALID_INPUT"
    env = evaluate(table(rules=[{"id": "r", "when": ["< x"], "then": ["a"]}]), {"x": 1})
    assert env["error"]["code"] == "INVALID_TABLE" and env["error"]["where"] == "rules[0].when[0]"
    assert evaluate(table(), {"x": float("nan")} if False else '{"x": NaN}')["error"]["code"] == "INVALID_JSON"
    assert evaluate(table(), '{"x":1,"x":2}')["error"]["code"] == "INVALID_JSON"
    assert evaluate(table(), {"x": 1}, expected_sha256="0" * 64)["error"]["code"] == "PIN_MISMATCH"
    pin = evaluate(table(), {"x": 1})["decision"]["sha256"]
    assert evaluate(table(), {"x": 1}, expected_sha256=pin.upper())["status"] == "MATCHED"


def test_envelope_details():
    env = evaluate(
        table(rules=[{"id": "r1", "when": ["< 5"], "then": ["low"]}, {"id": "r2", "when": [">= 5"], "then": ["high"]}]), '{"x": 7}'
    )
    assert env["status"] == "MATCHED" and env["outputs"] == {"o": "high"} and env["matched_rules"] == ["r2"]
    assert env["trace"][0] == {"rule": "r1", "matched": False, "failed": [{"input": "x", "test": "< 5", "result": "false"}]}
    assert env["inputs_used"] == {"x": 7}
    env = evaluate(table(), {"x": "seven"}, trace=False)
    assert env["trace"] is None and env["type_mismatches"] == [{"input": "x", "expected": "number", "value": '"seven"'}]
    env = evaluate(table(inputs=[{"id": "x", "expression": "a.b.c", "type": "number"}]), {"a": {"b": {}}})
    assert env["missing_inputs"] == ["a.b.c"] and env["status"] == "NO_MATCH"


def test_large_numbers_and_json_outputs():
    t = table(outputs=[{"name": "o", "type": "any"}], rules=[{"id": "r", "when": ["-"], "then": [{"route": ["a", "b"], "n": 1}]}])
    assert evaluate(t, {"x": 10**15})["result"] == {"route": ["a", "b"], "n": 1}


# ---------------------------------------------------------------- DMN XML


def test_xml_import_matches_json_semantics():
    t = load_table(XML, "eligibility")
    assert t.problems == [] and t.hit_policy == "PRIORITY" and t.source == "dmn-xml"
    assert [i.id for i in t.inputs] == ["in_score", "in_dti", "in_status"]
    assert t.outputs[0].values == ["DECLINE", "REFER", "APPROVE"] and t.outputs[0].default == "REFER"
    # The normalized JSON of the import is itself a valid table with the same pin.
    again = load_table(t.normalized())
    assert again.problems == [] and table_sha256(again.normalized()) == table_sha256(t.normalized())
    env = evaluate(XML, {"applicant": {"creditScore": 700, "dti": 0.2, "employment": "EMPLOYED"}}, decision_id="Loan eligibility")
    assert env["result"] == {"decision": "APPROVE", "reason": "near prime, salaried"}
    env = evaluate(XML, {"loan": {"amount": 150000, "channel": "BROKER"}}, decision_id="fees")
    assert env["result"] == 325.5


@pytest.mark.parametrize(
    "xml,code",
    [
        (XML, "AMBIGUOUS_DECISION"),
        ("<definitions xmlns='https://www.omg.org/spec/DMN/20191111/MODEL/'/>", "NO_DECISION_TABLE"),
        ("<?xml version='1.0'?><!DOCTYPE d [<!ENTITY x 'y'>]><definitions/>", "INVALID_XML"),
        ("<definitions><decision", "INVALID_XML"),
        ("<foo/>", "INVALID_XML"),
    ],
)
def test_xml_refusals(xml, code):
    assert evaluate(xml, {})["error"]["code"] == code


def test_xml_cell_errors_have_locations():
    bad = XML.replace("&lt; 580", "&lt; minScore").replace('"no income"', "concat(1)")
    t = load_table(bad, "eligibility")
    where = {p.where for p in t.problems}
    assert "rules[1].when[0]" in where and "rules[0].then[1]" in where
    assert evaluate(XML, {}, decision_id="nope")["error"]["code"] == "DECISION_NOT_FOUND"


# ---------------------------------------------------------------- checks


def test_check_findings():
    t = table(
        inputs=[{"id": "age", "expression": "age", "type": "number"}],
        rules=[
            {"id": "a", "when": ["< 30"], "then": ["x"]},
            {"id": "b", "when": ["[20..40]"], "then": ["y"]},
            {"id": "c", "when": ["[50..45]"], "then": ["z"]},
        ],
    )
    r = check(t)
    got = {(f["code"], tuple(f["rules"])) for f in r["findings"]}
    assert ("OVERLAP", ("a", "b")) in got and ("NEVER_MATCHES", ("c",)) in got
    assert any(f["code"] == "GAP" and "(40..+∞)" in f["message"] for f in r["findings"])
    assert not r["valid"] and r["decision"]["sha256"] == evaluate(t, {})["decision"]["sha256"]
    first = check(
        dict(t, hit_policy="FIRST", rules=[{"id": "a", "when": ["< 30"], "then": ["x"]}, {"id": "b", "when": ["[10..20]"], "then": ["y"]}])
    )
    assert [f["code"] for f in first["findings"]][:1] == ["SHADOWED"] and first["valid"]
    anyt = check(
        dict(t, hit_policy="ANY", rules=[{"id": "a", "when": ["< 30"], "then": ["x"]}, {"id": "b", "when": ["< 20"], "then": ["x"]}])
    )
    assert anyt["valid"] and not any(f["code"] == "CONFLICT" for f in anyt["findings"])
    coll = check(dict(t, hit_policy="COLLECT", outputs=[{"name": "o", "type": "string", "default": "d"}]))
    assert any(f["code"] == "DEFAULT_IGNORED" for f in coll["findings"])
    assert check("<foo/>")["findings"][0]["code"] == "INVALID_XML"
    assert check(XML, "eligibility")["valid"]


# ---------------------------------------------------------------- canonical JSON


def test_canonical_json_rfc8785_vectors():
    # RFC 8785 §3.2.2 number examples and key ordering by UTF-16 code units.
    assert (
        canonicalize([1e30, 4.5, 0.002, 1e-27, 333333333.3333333, 1e21, 1e20, -0.0])
        == "[1e+30,4.5,0.002,1e-27,333333333.3333333,1e+21,100000000000000000000,0]"
    )
    assert (
        canonicalize({"\u20ac": 1, "\r": 2, "\U0001f600": 3, "1": 4, "\u00f6": 5}) == '{"\\r":2,"1":4,"\u00f6":5,"\u20ac":1,"\U0001f600":3}'
    )
    assert canonicalize({"b": [True, None, "x\u0001"], "a": 1}) == '{"a":1,"b":[true,null,"x\\u0001"]}'
    with pytest.raises(ValueError):
        canonicalize(2**60)


def test_pin_is_stable_across_formatting():
    t = table()
    s1 = evaluate(t, {"x": 1})["decision"]["sha256"]
    s2 = evaluate(json.dumps(t, indent=4), {"x": 1})["decision"]["sha256"]
    s3 = evaluate(dict(t, hit_policy="U"), {"x": 1})["decision"]["sha256"]
    assert s1 == s2 == s3
    assert s1 != evaluate(table(rules=[{"id": "r1", "when": ["< 6"], "then": ["low"]}]), {"x": 1})["decision"]["sha256"]


def test_unexpected_exceptions_become_envelopes(monkeypatch):
    import importlib

    analysis = importlib.import_module("dmn_engine.analysis")
    ev = importlib.import_module("dmn_engine.evaluate")  # the module, not the re-exported function

    def boom(*a, **k):
        raise ZeroDivisionError

    monkeypatch.setattr(ev, "_run", boom)
    env = evaluate(table(), {"x": 1})
    assert env["status"] == "ERROR" and env["error"]["code"] == "INTERNAL_ERROR" and "ZeroDivisionError" in env["error"]["message"]
    monkeypatch.setattr(analysis, "check_table", boom)
    r = check(table())
    assert not r["valid"] and r["findings"][0]["code"] == "INTERNAL_ERROR"


def test_findings_cap_keeps_true_totals():
    many = table(outputs=[{"name": "o", "type": "number"}], rules=[{"id": f"r{i}", "when": ["-"], "then": [i]} for i in range(30)])
    r = check(many)
    assert len(r["findings"]) == 200 and r["truncated"] == 235 and r["counts"]["errors"] == 435


def test_examples_are_clean():
    fee = json.loads((ROOT / "examples" / "shipping-fee.json").read_text("utf-8"))
    r = check(fee)
    assert r["valid"] and r["counts"]["errors"] == 0, r["findings"]
    env = evaluate(fee, {"parcel": {"weight": 25, "region": "DOMESTIC", "express": True}})
    assert env["result"] == 8 + 6 + 15 + 4 + 10 and env["matched_rules"] == ["base", "heavy", "very-heavy", "domestic", "express"]
    for did in ("eligibility", "fees"):
        assert check(XML, did)["valid"], did
