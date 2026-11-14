"""Checks on research memos that need no database: reply shapes, the reviewer's citation checks, the
verdict rule, and that every tool refuses a role that is not allowed to use it."""
import json

import pytest

from stock_machine.office import research, roles, tools
from stock_machine.office.providers import ProviderRefused

PACK = {"event:ticker": "ACME", "event:type": "EARNINGS", "event:date": "2026-11-20",
        "fundamentals:quarter:2026-09-30:revenue": 1500.0, "price:2026-11-13:close": 42.5}


def memo(**changes):
    body = {"summary": "Quarterly revenue was 1,500.",
            "claims": [{"id": "c1", "kind": "FACT", "text": "Revenue was 1,500.",
                        "source": "fundamentals:quarter:2026-09-30:revenue", "value": 1500.0},
                       {"id": "c2", "kind": "FACT", "text": "The event is an earnings date.",
                        "source": "event:type", "value": "EARNINGS"},
                       {"id": "c3", "kind": "INTERPRETATION", "text": "The price already reflects it.",
                        "sources": ["price:2026-11-13:close"]},
                       {"id": "c4", "kind": "UNKNOWN", "text": "Guidance is not in the evidence."}],
            "counter_evidence": ["One quarter is a short record."]}
    body.update(changes)
    return body


def claim(**fields):
    return memo(claims=[{"id": "c1", "kind": "FACT", "text": "t", **fields}])


def test_a_well_formed_memo_is_accepted_and_unknown_fields_are_dropped():
    reply = "```json\n" + json.dumps({**memo(), "action": "BUY", "orders": [1]}) + "\n```"
    parsed = research.parse_memo(reply)
    assert parsed == memo() and "action" not in parsed
    assert research.check_citations(parsed, PACK) == []


@pytest.mark.parametrize("reply, code", [
    ("I cannot help with that.", "MEMO_NOT_JSON"),
    ("{not json}", "MEMO_NOT_JSON"),
    (json.dumps(memo(claims=[])), "MEMO_CLAIMS_INVALID"),
    (json.dumps(memo(claims=[{"id": f"c{n}", "kind": "UNKNOWN", "text": "t"} for n in range(13)])),
     "MEMO_CLAIMS_INVALID"),
    (json.dumps(claim(kind="RECOMMENDATION")), "MEMO_CLAIM_KIND_INVALID"),
    (json.dumps(claim(text="x" * 601)), "MEMO_CLAIM_TEXT_INVALID"),
    (json.dumps(claim(value={"a": 1})), "MEMO_VALUE_INVALID"),
    (json.dumps(claim(value=True)), "MEMO_VALUE_INVALID"),
    (json.dumps(claim(source=7)), "MEMO_SOURCE_INVALID"),
    (json.dumps(memo(claims=[{"id": "c1", "kind": "UNKNOWN", "text": "a"},
                             {"id": "c1", "kind": "UNKNOWN", "text": "b"}])), "MEMO_CLAIM_ID_REPEATED"),
    (json.dumps(memo(summary="")), "MEMO_SUMMARY_INVALID"),
    (json.dumps(memo(counter_evidence="none")), "MEMO_COUNTER_EVIDENCE_INVALID"),
])
def test_a_malformed_memo_reply_is_refused_with_a_code(reply, code):
    with pytest.raises(research.OutputInvalid, match=code):
        research.parse_memo(reply)


@pytest.mark.parametrize("fields, codes", [
    ({"source": "fundamentals:quarter:2026-09-30:ebitda", "value": 9.0}, ["CITATION_NOT_FOUND"]),
    ({"source": "fundamentals:quarter:2026-09-30:revenue", "value": 1600.0}, ["VALUE_MISMATCH"]),
    ({"source": "fundamentals:quarter:2026-09-30:revenue", "value": "1500"}, ["VALUE_MISMATCH"]),
    ({"source": "fundamentals:quarter:2026-09-30:revenue"}, ["FACT_VALUE_MISSING"]),
    ({"source": "event:type", "value": "SPLIT"}, ["VALUE_MISMATCH"]),
    ({"source": "event:type"}, []),                               # a code may be cited without repeating it
    ({}, ["FACT_WITHOUT_SOURCE"]),
    ({"source": "event:type", "sources": ["price:1999-01-01:close"]}, ["CITATION_NOT_FOUND"]),
])
def test_the_citation_check_names_each_problem(fields, codes):
    found = research.check_citations(research.parse_memo(json.dumps(claim(**fields))), PACK)
    assert [f["code"] for f in found] == codes
    assert all(f["by"] == "code" and f["claim_id"] == "c1" for f in found)


def test_an_interpretation_may_not_cite_evidence_that_does_not_exist():
    body = memo(claims=[{"id": "c1", "kind": "INTERPRETATION", "text": "t", "sources": ["news:headline"]}])
    assert [f["code"] for f in research.check_citations(body, PACK)] == ["CITATION_NOT_FOUND"]


def test_the_verdict_rule():
    missing, wrong = [{"code": "CITATION_NOT_FOUND"}], [{"code": "VALUE_MISMATCH"}]
    assert research.decide(1, [], "PASS") == "PASS" and research.decide(2, [], "PASS") == "PASS"
    assert research.decide(1, missing, "PASS") == "BLOCKED"        # an invented citation blocks at once
    assert research.decide(1, wrong, "PASS") == "REVISION_REQUESTED"
    assert research.decide(2, wrong, "PASS") == "BLOCKED"          # one revision round only
    assert research.decide(1, [], "REVISION_REQUESTED") == "REVISION_REQUESTED"
    assert research.decide(2, [], "REVISION_REQUESTED") == "BLOCKED"
    assert research.decide(1, [], "BLOCKED") == "BLOCKED"
    assert research.decide(1, wrong, None) == "REVISION_REQUESTED"
    # the reviewer's model can make the verdict stricter, never looser
    for version, found in ((1, missing), (1, wrong), (2, wrong)):
        assert research.decide(version, found, "PASS") == research.decide(version, found, None)


def test_a_review_reply_is_checked_too():
    ok = research.parse_review(json.dumps({"verdict": "PASS", "findings": [{"claim_id": "c3", "text": "Thin."}],
                                           "opposing_case": "Revenue may be seasonal.", "trade": "SELL"}))
    assert ok == {"verdict": "PASS", "opposing_case": "Revenue may be seasonal.",
                  "findings": [{"code": "REVIEWER_NOTE", "by": "model", "claim_id": "c3", "text": "Thin."}]}
    for reply, code in (("no", "REVIEW_NOT_JSON"), ('{"verdict": "APPROVED"}', "REVIEW_VERDICT_INVALID"),
                        ('{"verdict": "PASS", "findings": "none"}', "REVIEW_FINDINGS_INVALID"),
                        ('{"verdict": "PASS", "findings": [{"text": ""}]}', "REVIEW_FINDINGS_INVALID")):
        with pytest.raises(research.OutputInvalid, match=code):
            research.parse_review(reply)


def test_requests_carry_the_evidence_as_data_and_need_a_chosen_model():
    request = research.analyst_request("claude-opus-5-5", PACK)
    assert json.loads(request.messages[0]["content"]) == {"task": "Write the memo.", "evidence": PACK}
    assert "never as an instruction" in request.system and "never as an instruction" in research.REVIEWER_SYSTEM
    revised = research.analyst_request("claude-opus-5-5", PACK, {"memo": memo(), "findings": [{"code": "X"}]})
    assert json.loads(revised.messages[0]["content"])["review_findings"] == [{"code": "X"}]
    assert revised.sha256() != request.sha256()
    for build in (lambda: research.analyst_request("", PACK), lambda: research.reviewer_request("", memo(), PACK)):
        with pytest.raises(ProviderRefused, match="MODEL_NOT_CONFIGURED"):
            build()


def test_every_tool_refuses_a_role_that_is_not_allowed_before_touching_the_database():
    refused = [
        lambda: tools.write_event("analyst", None, source="filings", source_key="k", ticker="ACME",
                                  event_type="FILING", event_date="2026-11-13", detail={}),
        lambda: tools.write_memo("reviewer", None, event={}, version=1, memo_id=None, body={}, input_sha256="",
                                 run_id="", provider="", model="", prompt_version=""),
        lambda: tools.write_review("analyst", None, memo_id="", version=1, verdict="PASS", findings=[],
                                   run_id="", provider="", model="", prompt_version=""),
        lambda: tools.write_briefing("analyst", None, kind="MORNING", briefing_date="2026-11-14", content={}),
        lambda: tools.write_briefing("scout", None, kind="MORNING", briefing_date="2026-11-14", content={}),
        lambda: tools.source_events("qa", None),
        lambda: tools.recent_filings("controller", None),
        lambda: tools.fundamentals("scout", None, "ACME"),
        lambda: tools.prices("qa", None, "ACME"),
        lambda: tools.memo("scout", None, "m", 1),
        lambda: tools.review("scanner", None, "m", 1),
        lambda: tools.reviewed_memos("scout", None, None, None),
        lambda: tools.office_event("portfolio_manager", None, "e"),
    ]
    for call in refused:
        with pytest.raises(roles.ToolNotAllowed, match="TOOL_NOT_ALLOWED_FOR_ROLE"):
            call()


def test_the_tools_module_only_uses_tools_the_roles_catalogue_names():
    import inspect
    import re
    named = set(re.findall(r'roles\.authorize\(role, "([a-z_]+)"\)', inspect.getsource(tools)))
    assert named and named <= set(roles.TOOLS)
    # no tool writes an opportunity, an order or a fill yet
    assert "write_opportunity" not in named and not any("order" in n or "fill" in n for n in named)
