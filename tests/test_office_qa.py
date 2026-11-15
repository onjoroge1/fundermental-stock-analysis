"""QA scoring rules, checked without a database. Every ledger here is a test record, not a real result."""
import inspect
from datetime import date, timedelta

import pytest

from stock_machine.office import admission, config, qa, roles, tools

D = [str(date(2026, 1, 5) + timedelta(days=n)) for n in range(130)]
BLOCK = {"min_trades": 4, "min_dates": 4, "pass_mean_net_bps": 10}


def trade(n, signal, entered, exited, entry, exit_, *, ticker=None, side="LONG", shares=100, fee=1.0):
    """One closed simulated trade as the ledger tool returns it: an order, two fills and a position."""
    sign = 1 if side == "LONG" else -1
    net = (exit_ - entry) * shares * sign - 2 * fee
    oid = f"o{n}"
    return {
        "order": {"order_id": oid, "ticker": ticker or f"T{n}", "side": side, "shares": str(shares),
                  "signal_session": D[signal], "state": "FILLED"},
        "fills": [{"order_id": oid, "leg": "ENTRY", "fill_session": D[entered], "price": str(entry),
                   "shares": str(shares), "cost_usd": str(fee)},
                  {"order_id": oid, "leg": "EXIT", "fill_session": D[exited], "price": str(exit_),
                   "shares": str(shares), "cost_usd": str(fee)}],
        "position": {"order_id": oid, "ticker": ticker or f"T{n}", "side": side, "shares": str(shares),
                     "entry_session": D[entered], "entry_price": str(entry), "holding_sessions": 1,
                     "state": "CLOSED", "exit_session": D[exited], "exit_price": str(exit_),
                     "costs_usd": str(2 * fee), "realized_pnl_usd": str(net)},
        "opportunity": {"signal_session": D[signal], "ticker": ticker or f"T{n}", "status": "ACCEPTED",
                        "reason_codes": []},
    }


def ledger(trades, *, as_of=D[-1], protocol=None, extra_opportunities=(), mark="auto"):
    realized = sum(float(t["position"]["realized_pnl_usd"] or 0) for t in trades)
    if mark == "auto":
        mark = {"session": as_of, "equity_usd": str(100000 + realized), "realized_usd": str(realized),
                "unrealized_usd": "0", "stale_prices": [], "book_realized_usd": str(realized)}
    return {"deployment_id": "d", "strategy_id": "FIXTURE", "version": "v1", "book_id": "experimental",
            "state": "EXPERIMENTAL_PAPER", "execution_kind": "NATIVE",
            "forward_protocol": {"evaluation": BLOCK} if protocol is None else protocol,
            "starting_equity": "100000", "cost_model": {"bps_per_side": 10}, "as_of": as_of,
            "opportunities": [t["opportunity"] for t in trades] + list(extra_opportunities),
            "orders": [t["order"] for t in trades], "fills": [f for t in trades for f in t["fills"]],
            "positions": [t["position"] for t in trades], "mark": mark}


def separate(prices):
    """One trade per decision date, none overlapping: signal on day 3n, in and out on day 3n+1."""
    return [trade(n, 3 * n, 3 * n + 1, 3 * n + 1, 100, p) for n, p in enumerate(prices)]


# ------------------------------------------------------------------------------------- operational QA

def test_a_clean_book_has_no_defects_and_is_never_called_mature():
    out = qa.operational(ledger(separate([101, 102, 99])))
    assert out["verdict"] == "CONTINUE_COLLECTING" and out["maturity"] == "NOT_APPLICABLE"
    assert out["scorecard"]["defects"] == {} and out["scorecard"]["notes"] == {}
    assert out["scorecard"]["checked"] == {"orders": 3, "fills": 6, "positions": 3, "mark_session": D[-1]}
    assert "not broker fills" in out["scorecard"]["basis"]


def broken(change):
    book = ledger(separate([101, 102]))
    change(book)
    return qa.operational(book)


@pytest.mark.parametrize("code, change", [
    ("FILLED_ORDER_WITHOUT_ENTRY_FILL", lambda b: (b["fills"].pop(0), b["positions"].pop(0))),
    ("POSITION_WITHOUT_ENTRY_FILL", lambda b: b["fills"].pop(0)),
    ("ENTRY_FILL_WITHOUT_POSITION", lambda b: b["positions"].pop(0)),
    ("CLOSED_POSITION_WITHOUT_EXIT_FILL", lambda b: b["fills"].pop(1)),
    ("FILL_NOT_AFTER_SIGNAL", lambda b: b["orders"][0].update(signal_session=D[1])),
    ("FILL_DOES_NOT_MATCH_POSITION", lambda b: b["positions"][0].update(entry_price="100.5")),
    ("FILL_DOES_NOT_MATCH_POSITION", lambda b: b["fills"][1].update(fill_session=D[2])),
    ("COSTS_NOT_APPLIED", lambda b: b["fills"][0].update(cost_usd="0")),
    ("PNL_DOES_NOT_ADD_UP", lambda b: b["positions"][0].update(realized_pnl_usd="500")),
    ("PNL_DOES_NOT_ADD_UP", lambda b: b["positions"][0].update(costs_usd="0.5")),
    ("DUPLICATE_ORDER_FOR_SIGNAL", lambda b: b["orders"].append({**b["orders"][0], "order_id": "copy", "state": "EXPIRED"})),
    ("MARK_DOES_NOT_RECONCILE", lambda b: b["mark"].update(equity_usd="123456")),
    ("MARK_DOES_NOT_RECONCILE", lambda b: b["mark"].update(book_realized_usd="1")),
])
def test_each_kind_of_bookkeeping_defect_is_found_and_asks_for_a_pause(code, change):
    out = broken(change)
    assert code in out["scorecard"]["defects"], out["scorecard"]
    assert out["verdict"] == "PAUSE_FOR_DATA_OR_EXECUTION_DEFECT"


def test_missed_signals_and_stale_prices_are_reported_but_are_not_defects():
    extra = [{"signal_session": D[40], "ticker": "LATE", "status": "MISSED", "reason_codes": ["ENTRY_DEADLINE_PASSED"]},
             {"signal_session": D[41], "ticker": "OLD", "status": "REJECTED", "reason_codes": ["STALE_PRICE"]}]
    out = qa.operational(ledger(separate([101]), extra_opportunities=extra, mark=None))
    assert out["verdict"] == "CONTINUE_COLLECTING" and out["scorecard"]["defects"] == {}
    assert out["scorecard"]["notes"] == {"MISSED_SIGNALS": 1, "STALE_PRICE_REJECTIONS": 1, "NO_MARK_FOR_THIS_DATE": 1}


def test_a_free_book_is_not_faulted_for_charging_nothing():
    book = ledger([trade(0, 0, 1, 1, 100, 101, fee=0)])
    book["cost_model"] = {"bps_per_side": 0}
    assert qa.operational(book)["scorecard"]["defects"] == {}


# ---------------------------------------------------------------------------------- strategy evaluation

def test_figures_come_from_closed_trades_after_costs():
    out = qa.strategy(ledger(separate([101, 102, 99])))["scorecard"]["matured"]
    # nets: +98, +198, -102 on 10,000 each -> 98, 198, -102 bps
    assert (out["trades"], out["decision_dates"]) == (3, 3)
    assert (out["net_usd"], out["costs_usd"], out["gross_usd"]) == ("194.00", "6.00", "200.00")
    assert out["mean_net_bps_per_date"] == pytest.approx(64.67)
    assert out["max_drawdown_usd"] == "102.00" and out["max_drawdown_pct_of_starting_equity"] == 0.102
    assert out["largest_ticker_share_of_result"] == pytest.approx(198 / 398, abs=1e-4)
    assert out["turnover_times_starting_equity"] == 0.3


def test_without_registered_numbers_the_result_stays_pending_and_no_threshold_is_invented():
    out = qa.strategy(ledger(separate([110] * 40), protocol={"maturity": "prose only"}))
    assert (out["maturity"], out["verdict"]) == ("PENDING", "CONTINUE_COLLECTING")
    assert "PROTOCOL_HAS_NO_REGISTERED_NUMBERS" in out["limitations"]
    assert out["scorecard"]["registered"] is None and out["scorecard"]["matured"]["t_statistic_by_date"] is None


@pytest.mark.parametrize("block", [
    {"min_trades": 0, "min_dates": 4, "pass_mean_net_bps": 10}, {"min_trades": 4, "min_dates": 1, "pass_mean_net_bps": 10},
    {"min_trades": 4, "min_dates": 4}, {"min_trades": 4, "min_dates": 4, "pass_mean_net_bps": "10"},
    {"min_trades": 4, "min_dates": 4, "pass_mean_net_bps": 10, "pass_t": 0},
    {"min_trades": 4, "min_dates": 4, "pass_mean_net_bps": 10, "peek_again": True},
    {"min_trades": True, "min_dates": 4, "pass_mean_net_bps": 10}, "soon", []])
def test_a_malformed_registered_block_is_not_used(block):
    out = qa.strategy(ledger(separate([110] * 10), protocol={"evaluation": block}))
    assert out["maturity"] == "PENDING" and out["limitations"][0] == "PROTOCOL_REGISTERED_NUMBERS_INVALID"


def test_too_few_dates_stays_pending_and_gives_no_t_statistic():
    out = qa.strategy(ledger(separate([110, 111, 112])))
    assert (out["maturity"], out["verdict"]) == ("PENDING", "CONTINUE_COLLECTING")
    assert out["scorecard"]["matured"]["t_statistic_by_date"] is None
    assert "FEWER_TRADES_OR_DATES_THAN_REGISTERED" in out["limitations"]
    assert out["scorecard"]["registered"]["met"] is False


def test_many_trades_on_one_date_are_one_observation_and_never_mature():
    one_day = [trade(n, 0, 1, 1, 100, 105) for n in range(50)]
    out = qa.strategy(ledger(one_day))
    assert out["scorecard"]["matured"]["trades"] == 50 and out["scorecard"]["matured"]["decision_dates"] == 1
    assert out["maturity"] == "PENDING" and out["scorecard"]["matured"]["t_statistic_by_date"] is None
    assert "ALL_TRADES_FROM_ONE_DATE" in out["limitations"]


def test_a_mature_result_that_passes_asks_for_a_review_and_nothing_more():
    out = qa.strategy(ledger(separate([101, 102, 101.5, 103])))
    assert (out["maturity"], out["verdict"]) == ("MATURED", "REQUEST_QUALIFICATION_REVIEW")
    assert out["scorecard"]["matured"]["t_statistic_by_date"] > 2 and out["scorecard"]["registered"]["met"] is True


def test_a_mature_result_that_fails_retires_under_the_registered_rule():
    out = qa.strategy(ledger(separate([99, 100, 101, 99.5])))
    assert (out["maturity"], out["verdict"]) == ("MATURED", "RETIRE_UNDER_REGISTERED_RULE")


def test_a_registered_t_threshold_must_be_met_as_well_as_the_mean():
    protocol = {"evaluation": {**BLOCK, "pass_t": 50}}
    out = qa.strategy(ledger(separate([101, 102, 101.5, 103]), protocol=protocol))
    assert (out["maturity"], out["verdict"]) == ("MATURED", "RETIRE_UNDER_REGISTERED_RULE")


def test_overlapping_holdings_give_no_t_statistic_and_cannot_pass_a_t_test():
    held_long = [trade(n, n, n + 1, n + 6, 100, 103) for n in range(6)]        # each still open when the next enters
    plain = qa.strategy(ledger(held_long))
    assert plain["scorecard"]["matured"]["t_statistic_by_date"] is None
    assert "HOLDINGS_OVERLAP_SO_NO_T_STATISTIC" in plain["limitations"]
    assert plain["maturity"] == "MATURED"                                       # a mean-only rule can still be judged
    with_t = qa.strategy(ledger(held_long, protocol={"evaluation": {**BLOCK, "pass_t": 2}}))
    assert (with_t["maturity"], with_t["verdict"]) == ("PENDING", "CONTINUE_COLLECTING")
    assert "REGISTERED_TEST_CANNOT_BE_COMPUTED" in with_t["limitations"]


def test_open_positions_and_untaken_signals_are_counted_separately_and_never_scored():
    trades = separate([101, 102])
    still_open = trade(9, 30, 31, 31, 100, 500)
    still_open["position"].update(state="OPEN", exit_session=None, exit_price=None, realized_pnl_usd=None)
    still_open["fills"].pop()
    extra = [{"signal_session": D[40], "ticker": "", "status": "NO_OPPORTUNITY", "reason_codes": []},
             {"signal_session": D[41], "ticker": "BIG", "status": "REJECTED", "reason_codes": ["GROSS_EXPOSURE_LIMIT"]},
             {"signal_session": D[41], "ticker": "LATE", "status": "MISSED", "reason_codes": ["ENTRY_DEADLINE_PASSED"]}]
    card = qa.strategy(ledger(trades + [still_open], extra_opportunities=extra))["scorecard"]
    assert card["matured"]["trades"] == 2 and card["matured"]["net_usd"] == "296.00"      # the open 400% gain is not in it
    assert card["pending"] == {"open_positions": 1, "orders_waiting_for_a_fill": 0, "scored": False}
    assert card["shadow"] == {"signals_rejected": 1, "signals_missed": 1, "scored": False,
                              "reasons": {"ENTRY_DEADLINE_PASSED": 1, "GROSS_EXPOSURE_LIMIT": 1}}
    assert card["coverage"] == {"sessions_scanned": 5, "sessions_with_no_signal": 1, "sessions_with_a_signal": 4}


def test_a_trade_that_closed_after_the_scoring_date_is_pending_on_that_date():
    trades = separate([101, 102, 103])
    card = qa.strategy(ledger(trades, as_of=D[5]))["scorecard"]
    assert card["matured"]["trades"] == 2 and card["pending"]["open_positions"] == 1


def test_the_benchmark_is_shown_beside_the_result_only_when_every_window_has_prices():
    protocol = {"evaluation": {**BLOCK, "benchmark_ticker": "SPY"}}
    book = ledger(separate([101, 102]), protocol=protocol)
    shown = qa.strategy(book, [10.0, 30.0])
    assert shown["scorecard"]["benchmark"]["mean_bps_over_the_same_windows"] == 20.0
    missing = qa.strategy(book, [10.0, None])
    assert missing["scorecard"]["benchmark"] is None and "BENCHMARK_PRICES_MISSING" in missing["limitations"]
    assert "NO_BENCHMARK_REGISTERED_AS_A_TICKER" in qa.strategy(ledger(separate([101])))["limitations"]


def test_no_trades_is_a_valid_pending_result():
    out = qa.strategy(ledger([]))
    assert out["verdict"] == "CONTINUE_COLLECTING" and out["scorecard"]["matured"]["trades"] == 0
    assert out["scorecard"]["matured"]["mean_net_bps_per_date"] is None


# ------------------------------------------------------------------------------- proposals and limits

@pytest.mark.parametrize("change", [{}, {"Bad Key": 1}, {"a": [1]}, {"a": {"b": 1}}, {"a": "x" * 201}, {"a": float("nan")},
                                    {f"k{n}": 1 for n in range(11)}, "tighten the stop", None])
def test_a_proposal_change_is_a_short_flat_list_of_named_values(change):
    with pytest.raises(ValueError, match="PROPOSAL_CHANGE_INVALID"):
        qa.check_change(change)
    assert qa.check_change({"min_gap_atr": 2.5, "note": "wider gap only", "shorts": False})


def test_qa_and_state_changes_do_nothing_while_the_office_is_off(monkeypatch):
    monkeypatch.delenv("OFFICE_ENABLED", raising=False)
    for call in (lambda: qa.evaluate("d", date.today()), lambda: qa.accept_proposal("p", accepted_by="owner"),
                 lambda: qa.propose("S", "v1", "v2", reason="a reason long enough", change={"a": 1}),
                 lambda: admission.apply_evaluation("e"),
                 lambda: admission.set_state("d", "PAUSED", owner="owner", reason_code="OWNER_PAUSE")):
        with pytest.raises(config.OfficeDisabled):
            call()


def test_the_owner_can_only_pause_resume_or_retire(monkeypatch):
    monkeypatch.setenv("OFFICE_ENABLED", "true")
    for state, reason in (("QUALIFIED_PAPER", "OWNER_CHOICE"), ("LIVE", "OWNER_CHOICE"), ("BLOCKED", "OWNER_CHOICE"),
                          ("PAUSED", "lower case"), ("PAUSED", "")):
        with pytest.raises(ValueError, match="STATE_CHANGE_NOT_ALLOWED"):
            admission.set_state("d", state, owner="owner", reason_code=reason)
    assert not any("QUALIFIED" in state or "LIVE" in state for pair in tools.STATE_CHANGES for state in pair)
    assert set(admission.VERDICT_STATE.values()) == {"PAUSED", "RETIRED"}


def test_qa_cannot_change_a_deployment_and_only_qa_writes_scorecards():
    with pytest.raises(roles.ToolNotAllowed):
        tools.change_deployment_state("qa", None, deployment_id="d", to_state="PAUSED", actor="policy",
                                      reason_code="X_Y_Z", policy_version="p")
    for role in ("portfolio_manager", "scanner", "analyst", "reviewer"):
        with pytest.raises(roles.ToolNotAllowed):
            tools.write_evaluation(role, None, deployment_id="d", kind="STRATEGY", as_of=None, maturity="PENDING",
                                   verdict="CONTINUE_COLLECTING", scorecard={}, limitations=[], protocol={},
                                   policy_version="p", input_sha256="0" * 64)
        with pytest.raises(roles.ToolNotAllowed):
            tools.write_proposal(role, None, strategy_id="S", version="v1", proposed_version="v2", reason="r",
                                 change={}, evaluation_id=None)


def test_qa_has_no_model_no_network_and_changes_no_ledger_row():
    source = inspect.getsource(qa)
    assert "providers" not in source and "http" not in source.lower() and "requests" not in source
    for table in ("office_orders", "office_fills", "office_positions", "office_marks", "office_opportunities",
                  "office_deployments SET", "office_evaluations SET"):
        assert f"UPDATE {table}" not in source and f"DELETE FROM {table}" not in source and f"INSERT INTO {table}" not in source
    assert "UPDATE" not in source and "DELETE" not in source
    assert source.count("INSERT INTO") == 1 and "INSERT INTO office_strategy_versions" in source
