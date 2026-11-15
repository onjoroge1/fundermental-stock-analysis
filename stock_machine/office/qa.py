"""QA: score what the paper book actually did, separately from how it was run, and propose new versions.

QA only reads the paper ledger. Signals, simulated fills, positions and marks are written once and nothing
here can change them. Every figure is from simulated fills on daily bars; none is a broker fill.

Two assessments are written for a deployment and a date, and they never share a verdict:

* ``operational`` asks whether the book was run correctly: every filled order has its fill and position,
  fills come after their signals, costs were charged, profit and loss add up, and the mark reconciles.
  A defect gives PAUSE_FOR_DATA_OR_EXECUTION_DEFECT.
* ``strategy`` asks what the frozen rule produced after costs on trades that have closed. Open positions
  are counted as pending and signals that were not taken as shadow; neither is scored.

A strategy result stays PENDING until the deployment's forward protocol says it is mature. The protocol
must carry those numbers itself, in an ``evaluation`` block registered before the first signal:

    {"min_trades": 100, "min_dates": 30, "pass_mean_net_bps": 10, "pass_t": 2.0, "benchmark_ticker": "SPY"}

(``pass_t`` and ``benchmark_ticker`` are optional.) Without that block QA invents no threshold: the result
stays PENDING. The registered evaluation is run once. When it is mature it either asks for a qualification
review or retires the deployment under its own rule; it is never repeated on a later date.

The unit of evidence is the decision date, not the trade. Trades from one date are averaged first. No t
statistic is given from fewer dates than the protocol registers, or when trades from different dates
overlap in time, because those are not independent observations.

QA cannot pause, retire or qualify anything. It writes a verdict; ``admission.apply_evaluation`` acts on it.
A proposal is an idea for a new version. Accepting one records a new, inadmissible inventory row with no
evidence; the version it came from, its history and its forward test are left exactly as they were.
"""
from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal

from psycopg.types.json import Jsonb

from . import config, tasks, tools

ROLE = "qa"
POLICY_VERSION = "qa.v1"
BASIS = "simulated paper fills on daily bars; not broker fills"
CENT = Decimal("0.01")
DEFECTS = ("FILLED_ORDER_WITHOUT_ENTRY_FILL", "ENTRY_FILL_WITHOUT_POSITION", "POSITION_WITHOUT_ENTRY_FILL",
           "CLOSED_POSITION_WITHOUT_EXIT_FILL", "FILL_NOT_AFTER_SIGNAL", "FILL_DOES_NOT_MATCH_POSITION",
           "COSTS_NOT_APPLIED", "PNL_DOES_NOT_ADD_UP", "DUPLICATE_ORDER_FOR_SIGNAL", "MARK_DOES_NOT_RECONCILE")
PROPOSAL_NEEDS = ("FROZEN_RULES", "DEVELOPMENT_RESULT", "NEW_FORWARD_PROTOCOL", "NEW_ADMISSION_DECISION")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


def _d(value) -> Decimal:
    return Decimal(str(value))


# ------------------------------------------------------------------------------------- operational QA

def operational(ledger: dict) -> dict:
    """Was the book run correctly? Pure: it reads only the ledger it is given."""
    orders = {o["order_id"]: o for o in ledger["orders"]}
    positions = {p["order_id"]: p for p in ledger["positions"]}
    fills: dict[tuple[str, str], dict] = {(f["order_id"], f["leg"]): f for f in ledger["fills"]}
    as_of = ledger["as_of"]
    charged = _d(ledger["cost_model"].get("bps_per_side", 0) or 0) > 0
    found: dict[str, int] = {}

    def defect(code: str, hit: bool) -> None:
        if hit:
            found[code] = found.get(code, 0) + 1

    seen = set()
    for order in orders.values():
        key = (order["ticker"], order["signal_session"])
        defect("DUPLICATE_ORDER_FOR_SIGNAL", key in seen)
        seen.add(key)
        entry = fills.get((order["order_id"], "ENTRY"))
        defect("FILLED_ORDER_WITHOUT_ENTRY_FILL", order["state"] == "FILLED" and entry is None)
        if entry is not None:
            defect("FILL_NOT_AFTER_SIGNAL", entry["fill_session"] <= order["signal_session"])
            defect("ENTRY_FILL_WITHOUT_POSITION", order["order_id"] not in positions)
    for position in positions.values():
        entry = fills.get((position["order_id"], "ENTRY"))
        exit_ = fills.get((position["order_id"], "EXIT"))
        closed = position["state"] == "CLOSED" and position["exit_session"] <= as_of
        defect("POSITION_WITHOUT_ENTRY_FILL", entry is None)
        defect("CLOSED_POSITION_WITHOUT_EXIT_FILL", closed and exit_ is None)
        if entry is None:
            continue
        defect("FILL_DOES_NOT_MATCH_POSITION",
               _d(entry["price"]) != _d(position["entry_price"]) or _d(entry["shares"]) != _d(position["shares"])
               or entry["fill_session"] != position["entry_session"])
        defect("COSTS_NOT_APPLIED", charged and _d(entry["cost_usd"]) <= 0)
        if closed and exit_ is not None:
            defect("FILL_DOES_NOT_MATCH_POSITION", _d(exit_["price"]) != _d(position["exit_price"])
                   or exit_["fill_session"] != position["exit_session"])
            defect("COSTS_NOT_APPLIED", charged and _d(exit_["cost_usd"]) <= 0)
            costs = _d(entry["cost_usd"]) + _d(exit_["cost_usd"])
            sign = 1 if position["side"] == "LONG" else -1
            expected = (_d(position["exit_price"]) - _d(position["entry_price"])) * _d(position["shares"]) * sign - costs
            defect("PNL_DOES_NOT_ADD_UP", abs(expected - _d(position["realized_pnl_usd"])) > CENT
                   or abs(costs - _d(position["costs_usd"])) > CENT)
    mark = ledger["mark"]
    if mark is not None:
        equity = _d(ledger["starting_equity"]) + _d(mark["realized_usd"]) + _d(mark["unrealized_usd"])
        defect("MARK_DOES_NOT_RECONCILE", abs(equity - _d(mark["equity_usd"])) > CENT
               or abs(_d(mark["realized_usd"]) - _d(mark["book_realized_usd"])) > CENT)

    statuses = [o["status"] for o in ledger["opportunities"]]
    notes = {
        "MISSED_SIGNALS": statuses.count("MISSED"),
        "EXPIRED_ORDERS": sum(o["state"] == "EXPIRED" for o in orders.values()),
        "STALE_PRICE_REJECTIONS": sum("STALE_PRICE" in o["reason_codes"] for o in ledger["opportunities"]),
        "STALE_PRICES_IN_MARK": 0 if mark is None else len(mark["stale_prices"]),
        "NO_MARK_FOR_THIS_DATE": int(mark is None or mark["session"] != as_of),
    }
    verdict = "PAUSE_FOR_DATA_OR_EXECUTION_DEFECT" if found else "CONTINUE_COLLECTING"
    return {"maturity": "NOT_APPLICABLE", "verdict": verdict, "limitations": [],
            "scorecard": {"basis": BASIS, "defects": dict(sorted(found.items())),
                          "notes": {k: v for k, v in notes.items() if v},
                          "checked": {"orders": len(orders), "fills": len(ledger["fills"]),
                                      "positions": len(positions), "mark_session": mark and mark["session"]}}}


# ---------------------------------------------------------------------------------- strategy evaluation

def registered(protocol: dict) -> tuple[dict | None, str | None]:
    """The machine-readable evaluation block of a forward protocol, or the reason it cannot be used."""
    block = protocol.get("evaluation")
    if block is None:
        return None, "PROTOCOL_HAS_NO_REGISTERED_NUMBERS"

    def whole(value):
        return type(value) is int and value >= 1

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    ok = (isinstance(block, dict)
          and set(block) <= {"min_trades", "min_dates", "pass_mean_net_bps", "pass_t", "benchmark_ticker"}
          and whole(block.get("min_trades")) and whole(block.get("min_dates")) and block["min_dates"] >= 2
          and number(block.get("pass_mean_net_bps"))
          and ("pass_t" not in block or (number(block["pass_t"]) and block["pass_t"] > 0))
          and ("benchmark_ticker" not in block or isinstance(block["benchmark_ticker"], str)))
    return (block, None) if ok else (None, "PROTOCOL_REGISTERED_NUMBERS_INVALID")


def matured_trades(ledger: dict) -> list[dict]:
    """Closed positions as of the ledger date, each with its decision date and its net result."""
    signal = {o["order_id"]: o["signal_session"] for o in ledger["orders"]}
    out = []
    for p in ledger["positions"]:
        if p["state"] != "CLOSED" or p["exit_session"] > ledger["as_of"] or p["order_id"] not in signal:
            continue
        notional = _d(p["shares"]) * _d(p["entry_price"])
        net, costs = _d(p["realized_pnl_usd"]), _d(p["costs_usd"])
        out.append({"ticker": p["ticker"], "decision_date": signal[p["order_id"]], "entered": p["entry_session"],
                    "exited": p["exit_session"], "notional": notional, "net": net, "costs": costs,
                    "net_bps": float(net / notional * 10_000) if notional else 0.0})
    return out


def _share(values: dict[str, Decimal]) -> float | None:
    total = sum(abs(v) for v in values.values())
    return None if not total else round(float(max(abs(v) for v in values.values()) / total), 4)


def _overlap(trades: list[dict]) -> bool:
    """True if a trade from one decision date was still open when a later date's trade was entered."""
    spans = sorted({(t["decision_date"], t["entered"], t["exited"]) for t in trades}, key=lambda s: s[1])
    latest_exit, latest_date = "", None
    for decided, entered, exited in spans:
        if latest_date not in (None, decided) and entered <= latest_exit:
            return True
        if exited > latest_exit:
            latest_exit, latest_date = exited, decided
    return False


def strategy(ledger: dict, benchmark: list[float | None] | None = None) -> dict:
    """What did the frozen rule produce after costs on closed trades? Pure: it reads only what it is given.

    ``benchmark`` is the benchmark's return in basis points over each matured trade's window, in order.
    """
    trades = matured_trades(ledger)
    block, problem = registered(ledger["forward_protocol"])
    limitations = [problem] if problem else []
    by_date: dict[str, list[float]] = {}
    for t in trades:
        by_date.setdefault(t["decision_date"], []).append(t["net_bps"])
    date_means = [sum(v) / len(v) for _, v in sorted(by_date.items())]
    n = len(date_means)
    mean = sum(date_means) / n if n else None

    net = sum((t["net"] for t in trades), Decimal(0))
    costs = sum((t["costs"] for t in trades), Decimal(0))
    running = peak = worst = Decimal(0)
    for t in sorted(trades, key=lambda t: (t["exited"], t["ticker"])):
        running += t["net"]
        peak = max(peak, running)
        worst = max(worst, peak - running)
    start = _d(ledger["starting_equity"])
    by_ticker: dict[str, Decimal] = {}
    by_day: dict[str, Decimal] = {}
    for t in trades:
        by_ticker[t["ticker"]] = by_ticker.get(t["ticker"], Decimal(0)) + t["net"]
        by_day[t["decision_date"]] = by_day.get(t["decision_date"], Decimal(0)) + t["net"]

    enough = block is not None and len(trades) >= block["min_trades"] and n >= block["min_dates"]
    overlapping = _overlap(trades)
    t_stat = None
    if enough and not overlapping and n >= 2:
        variance = sum((x - mean) ** 2 for x in date_means) / (n - 1)
        if variance > 0:
            t_stat = round(mean / math.sqrt(variance / n), 2)
    if block is not None and not enough:
        limitations.append("FEWER_TRADES_OR_DATES_THAN_REGISTERED")
    if enough and overlapping:
        limitations.append("HOLDINGS_OVERLAP_SO_NO_T_STATISTIC")
    if enough and not overlapping and t_stat is None:
        limitations.append("NO_VARIATION_SO_NO_T_STATISTIC")
    if len(by_date) == 1:
        limitations.append("ALL_TRADES_FROM_ONE_DATE")

    maturity, verdict = "PENDING", "CONTINUE_COLLECTING"
    if enough and "pass_t" in block and t_stat is None:
        limitations.append("REGISTERED_TEST_CANNOT_BE_COMPUTED")
    elif enough:
        passed = mean >= block["pass_mean_net_bps"] and ("pass_t" not in block or t_stat >= block["pass_t"])
        maturity = "MATURED"
        verdict = "REQUEST_QUALIFICATION_REVIEW" if passed else "RETIRE_UNDER_REGISTERED_RULE"

    compared = None
    if benchmark is not None:
        usable = [b for b in benchmark if b is not None]
        if len(usable) != len(trades):
            limitations.append("BENCHMARK_PRICES_MISSING")
        elif trades:
            compared = {"ticker": block["benchmark_ticker"],
                        "mean_bps_over_the_same_windows": round(sum(usable) / len(usable), 2),
                        "basis": "bought at each entry session's open, sold at each exit session's close, no costs"}
    elif block is None or "benchmark_ticker" not in block:
        limitations.append("NO_BENCHMARK_REGISTERED_AS_A_TICKER")

    statuses = [o["status"] for o in ledger["opportunities"]]
    sessions = {o["signal_session"] for o in ledger["opportunities"]}
    quiet = {o["signal_session"] for o in ledger["opportunities"] if o["status"] == "NO_OPPORTUNITY"}
    reasons: dict[str, int] = {}
    for o in ledger["opportunities"]:
        if o["status"] in ("REJECTED", "MISSED"):
            for code in o["reason_codes"]:
                reasons[code] = reasons.get(code, 0) + 1
    scorecard = {
        "basis": BASIS,
        "matured": {
            "trades": len(trades), "decision_dates": n,
            "net_usd": str(net.quantize(CENT)), "costs_usd": str(costs.quantize(CENT)),
            "gross_usd": str((net + costs).quantize(CENT)),
            "mean_net_bps_per_date": None if mean is None else round(mean, 2),
            "t_statistic_by_date": t_stat,
            "max_drawdown_usd": str(worst.quantize(CENT)),
            "max_drawdown_pct_of_starting_equity": round(float(worst / start * 100), 4),
            "largest_ticker_share_of_result": _share(by_ticker),
            "largest_date_share_of_result": _share(by_day),
            "turnover_times_starting_equity": round(float(sum((t["notional"] for t in trades), Decimal(0)) / start), 4),
        },
        "pending": {"open_positions": sum(p["state"] == "OPEN" or p["exit_session"] > ledger["as_of"]
                                          for p in ledger["positions"]),
                    "orders_waiting_for_a_fill": sum(o["state"] == "PENDING" for o in ledger["orders"]),
                    "scored": False},
        "shadow": {"signals_rejected": statuses.count("REJECTED"), "signals_missed": statuses.count("MISSED"),
                   "reasons": dict(sorted(reasons.items())), "scored": False},
        "coverage": {"sessions_scanned": len(sessions), "sessions_with_no_signal": len(quiet),
                     "sessions_with_a_signal": len(sessions - quiet)},
        "benchmark": compared,
        "registered": None if block is None else {**block, "met": enough},
    }
    return {"maturity": maturity, "verdict": verdict, "limitations": limitations, "scorecard": scorecard}


# ------------------------------------------------------------------------------------------- writing

def evaluate(deployment_id: str, as_of: date) -> dict:
    """Write both assessments of one deployment for one date. Repeating it returns what is already stored."""
    config.require_enabled()
    if as_of > date.today():
        raise ValueError("AS_OF_IN_THE_FUTURE")
    with tasks.connect() as conn:
        ledger = tools.deployment_ledger(ROLE, conn, deployment_id, as_of)
        if ledger is None:
            raise ValueError("DEPLOYMENT_NOT_FOUND")
        if ledger["execution_kind"] != "NATIVE":
            raise ValueError("DEPLOYMENT_IS_SCORED_BY_ITS_OWN_RECORDER")
        digest = tools.sha256(ledger)
        common = dict(deployment_id=deployment_id, as_of=as_of, protocol=ledger["forward_protocol"],
                      policy_version=POLICY_VERSION, input_sha256=digest)
        out = {"operational": tools.write_evaluation(ROLE, conn, kind="OPERATIONAL", **operational(ledger), **common)}
        done = tools.matured_evaluation(ROLE, conn, deployment_id)
        if done is not None:                       # the registered evaluation has been run; it is not run again
            out["strategy"] = {**done, "created": False, "already_evaluated": True}
            return out
        block, _ = registered(ledger["forward_protocol"])
        benchmark = None
        if block is not None and "benchmark_ticker" in block:
            benchmark = tools.benchmark_outcomes(ROLE, conn, block["benchmark_ticker"],
                                                 [(t["entered"], t["exited"]) for t in matured_trades(ledger)])
        out["strategy"] = tools.write_evaluation(ROLE, conn, kind="STRATEGY", **strategy(ledger, benchmark), **common)
    return out


def check_change(change) -> dict:
    """A proposal's change is a short flat list of named values, so it cannot carry anything else."""
    ok = (isinstance(change, dict) and 1 <= len(change) <= 10
          and all(isinstance(k, str) and _KEY.match(k) for k in change)
          and all((type(v) is str and 1 <= len(v) <= 200) or type(v) is bool
                  or (type(v) in (int, float) and math.isfinite(v)) for v in change.values()))
    if not ok:
        raise ValueError("PROPOSAL_CHANGE_INVALID")
    return change


def propose(strategy_id: str, version: str, proposed_version: str, *, reason: str, change: dict,
            evaluation_id: str | None = None) -> dict:
    """Record an idea for a new version of a strategy. The version it starts from is not touched."""
    config.require_enabled()
    reason = " ".join(str(reason).split())
    if not _ID.match(proposed_version or "") or proposed_version == version or not 10 <= len(reason) <= 500:
        raise ValueError("PROPOSAL_INVALID")
    check_change(change)
    with tasks.connect() as conn:
        if tools.strategy_evidence(ROLE, conn, strategy_id, version) is None:
            raise ValueError("STRATEGY_NOT_IN_INVENTORY")
        if tools.strategy_evidence(ROLE, conn, strategy_id, proposed_version) is not None:
            raise ValueError("PROPOSED_VERSION_ALREADY_USED")
        return {**tools.write_proposal(ROLE, conn, strategy_id=strategy_id, version=version,
                                       proposed_version=proposed_version, reason=reason, change=change,
                                       evaluation_id=evaluation_id),
                "needs": list(PROPOSAL_NEEDS)}


def accept_proposal(proposal_id: str, *, accepted_by: str) -> dict:
    """Record a proposed version in the inventory as an idea with no evidence. Done by the owner, in code:
    no role can write an inventory row. The new row is not admissible and has no execution."""
    config.require_enabled()
    with tasks.connect() as conn:
        row = conn.execute(
            """SELECT p.strategy_id, p.version, p.proposed_version, p.reason, p.change, s.source_repository,
                      s.risk_group FROM office_proposals p
               JOIN office_strategy_versions s ON s.strategy_id = p.strategy_id AND s.version = p.version
               WHERE p.proposal_id = %s""", (proposal_id,)).fetchone()
        if row is None:
            raise ValueError("PROPOSAL_NOT_FOUND")
        strategy_id, parent, new, reason, change, repository, risk_group = row
        record = {"rule_summary": "Not written yet.", "hashes": {}, "development": {}, "shorts": False,
                  "defects": [], "forward_protocol": None, "runner_status": "None.",
                  "display_notes": f"An idea taken from {strategy_id} {parent}. It has not been tested.",
                  "exclusion_reason": "No evidence yet. It needs frozen rules, a development result, a new "
                                      "forward protocol and its own admission decision.",
                  "proposal": {"proposal_id": str(proposal_id), "from_version": parent, "reason": reason,
                               "change": change, "needs": list(PROPOSAL_NEEDS)}}
        columns = (strategy_id, new, repository, "NONE", f"Proposed from {parent}; no evidence", False, "NONE",
                   risk_group)
        done = conn.execute(
            """INSERT INTO office_strategy_versions(strategy_id, version, source_repository, evidence_tier,
                   original_status, admissible, execution_kind, risk_group, record, record_sha256)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (strategy_id, version) DO NOTHING RETURNING strategy_id""",
            (*columns, Jsonb({**record, "accepted_by": accepted_by}),
             tools.sha256({"columns": columns, "record": record}))).fetchone()
        if done is None and conn.execute(
                "SELECT record->'proposal'->>'proposal_id' FROM office_strategy_versions WHERE strategy_id = %s "
                "AND version = %s", (strategy_id, new)).fetchone()[0] != str(proposal_id):
            raise ValueError("PROPOSED_VERSION_ALREADY_USED")
    return {"status": "CREATED" if done is not None else "PRESENT", "strategy_id": strategy_id, "version": new,
            "from_version": parent, "admissible": False, "needs": list(PROPOSAL_NEEDS)}


def latest(conn) -> dict:
    """For the status page: each deployment's newest assessment of each kind, and proposals not yet accepted."""
    rows = conn.execute(
        """SELECT DISTINCT ON (e.deployment_id, e.kind) d.strategy_id, d.version, d.state, e.kind, e.as_of,
                  e.maturity, e.verdict, e.scorecard, e.limitations
           FROM office_evaluations e JOIN office_deployments d ON d.deployment_id = e.deployment_id
           ORDER BY e.deployment_id, e.kind, e.as_of DESC""").fetchall()
    out = []
    for strategy_id, version, state, kind, as_of, maturity, verdict, scorecard, limitations in rows:
        item = {"strategy_id": strategy_id, "version": version, "deployment_state": state, "kind": kind,
                "as_of": str(as_of), "maturity": maturity, "verdict": verdict, "limitations": limitations,
                "simulated": True}
        if kind == "OPERATIONAL":
            item["defects"] = scorecard.get("defects", {})
        else:
            matured = scorecard.get("matured", {})
            item.update(trades=matured.get("trades"), decision_dates=matured.get("decision_dates"),
                        net_usd=matured.get("net_usd"), open_positions=scorecard.get("pending", {}).get("open_positions"))
        out.append(item)
    waiting = conn.execute(
        """SELECT count(*) FROM office_proposals p WHERE NOT EXISTS (
               SELECT 1 FROM office_strategy_versions s WHERE s.strategy_id = p.strategy_id
                 AND s.version = p.proposed_version)""").fetchone()[0]
    return {"evaluations": out, "proposals_waiting": waiting}
