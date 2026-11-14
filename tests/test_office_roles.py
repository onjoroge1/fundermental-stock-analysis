"""Who may do what in the office. These rules are fixed before any tool exists."""
import hashlib
import json

import pytest

from stock_machine.office import roles
from stock_machine.office.roles import READ, ROLES, TOOLS, WRITE

# A change to any role's tools must be deliberate: bump the role's version and update this pin.
PINNED = {
    "controller": (1, "5430defd646204e6"),
    "scout": (1, "35a4b6b6c97b30f9"),
    "analyst": (1, "11660f7193894e86"),
    "reviewer": (1, "476d3df20ee0cd34"),
    "portfolio_manager": (1, "f9c4a395b756fea3"),
    "scanner": (1, "5cf237533db984b0"),
    "qa": (1, "acdb3c581af642f8"),
}


def digest(role):
    return hashlib.sha256(json.dumps(ROLES[role].definition(), sort_keys=True).encode()).hexdigest()[:16]


def test_a_role_cannot_change_without_a_new_version_and_a_new_pin():
    assert {name: (role.version, digest(name)) for name, role in ROLES.items()} == PINNED


def test_every_tool_is_a_typed_read_or_a_write_to_one_office_table():
    for name, (kind, table) in TOOLS.items():
        assert kind in (READ, WRITE), name
        assert name.startswith(kind + "_"), name
        assert (table is None) if kind == READ else table.startswith("office_"), name
    written = [t for _, t in TOOLS.values() if t]
    assert len(written) == len(set(written))               # one tool per table: no second way in


def test_no_tool_offers_a_shell_sql_credentials_the_web_a_broker_or_an_order():
    for name in TOOLS:
        parts = name.split("_")
        assert not set(parts) & set(roles.FORBIDDEN_WORDS), name
    assert not any(t in ("office_orders", "office_fills", "office_positions", "office_strategy_versions")
                   for _, t in TOOLS.values())             # code writes these, never a role


def test_roles_only_use_known_tools_and_have_no_duplicates():
    assert set(ROLES) == {"controller", "scout", "analyst", "reviewer", "portfolio_manager", "scanner", "qa"}
    for role in ROLES.values():
        assert set(role.tools) <= set(TOOLS), role.name
        assert len(role.tools) == len(set(role.tools)), role.name


def test_author_and_reviewer_are_separate():
    assert roles.writes("analyst") == {"office_memos"}
    assert roles.writes("reviewer") == {"office_reviews"}
    assert "write_memo" not in ROLES["reviewer"].tools and "write_review" not in ROLES["analyst"].tools
    assert [r for r in ROLES if "write_memo" in ROLES[r].tools] == ["analyst"]
    assert [r for r in ROLES if "write_review" in ROLES[r].tools] == ["reviewer"]


def test_only_the_portfolio_manager_decides_admission_and_deployment_state():
    for tool in ("write_admission_decision", "write_deployment_state"):
        assert [r for r in ROLES if tool in ROLES[r].tools] == ["portfolio_manager"]
    assert "write_evaluation" not in ROLES["portfolio_manager"].tools   # it cannot score its own book


def test_qa_and_reviewer_cannot_change_strategies_or_the_book():
    assert roles.writes("qa") == {"office_evaluations", "office_proposals", "office_briefings"}
    for role in ("qa", "reviewer", "analyst", "scout"):
        assert not roles.writes(role) & {"office_admission_decisions", "office_deployments",
                                         "office_opportunities"}, role
    assert [r for r in ROLES if "write_evaluation" in ROLES[r].tools] == ["qa"]


def test_code_only_roles_never_call_a_model():
    assert {r for r in ROLES if not ROLES[r].may_call_model} == {"controller", "scout", "scanner"}
    assert roles.writes("scanner") == {"office_opportunities"} and roles.writes("controller") == set()


def test_authorize_refuses_everything_not_listed():
    roles.authorize("analyst", "write_memo")
    roles.authorize("reviewer", "read_memo")
    for role, tool, code in (("analyst", "write_review", "TOOL_NOT_ALLOWED_FOR_ROLE"),
                             ("reviewer", "write_memo", "TOOL_NOT_ALLOWED_FOR_ROLE"),
                             ("qa", "write_admission_decision", "TOOL_NOT_ALLOWED_FOR_ROLE"),
                             ("analyst", "run_shell", "UNKNOWN_TOOL"), ("analyst", "", "UNKNOWN_TOOL"),
                             ("owner", "read_memo", "UNKNOWN_ROLE"), ("", "read_memo", "UNKNOWN_ROLE")):
        with pytest.raises(roles.ToolNotAllowed, match=code):
            roles.authorize(role, tool)


def test_definitions_are_stable_content_for_the_immutable_register(monkeypatch):
    recorded = []
    monkeypatch.setattr(roles.tasks, "register_definition",
                        lambda role, version, provider, model, definition: recorded.append(
                            (role, version, provider, model, definition)) or {"role": role})
    roles.register_all("anthropic", "claude-opus-5-5")
    assert [r[0] for r in recorded] == list(ROLES)
    by_role = {r[0]: r for r in recorded}
    assert by_role["analyst"][2:4] == ("anthropic", "claude-opus-5-5")
    assert by_role["scanner"][2:4] == ("none", "") and by_role["controller"][2:4] == ("none", "")
    assert by_role["reviewer"][4]["tools"] == sorted(ROLES["reviewer"].tools)
