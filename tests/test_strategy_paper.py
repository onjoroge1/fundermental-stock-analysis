from stock_machine import strategy_paper


class _Cursor:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))


class _Connection:
    def __init__(self):
        self.calls = []
        self.commits = 0

    def cursor(self):
        return _Cursor(self.calls)

    def commit(self):
        self.commits += 1


def _screen():
    return {"status": "OK", "execution_status": "PAPER_ONLY",
            "as_of": "2026-08-21", "policies": {"value_quality": {
                "status": "PAPER_ELIGIBLE", "candidates": [
                    {"ticker": "A", "target_weight": 0.5},
                    {"ticker": "B", "target_weight": 0.5},
                ]}}}


def test_strategy_paper_sync_is_idempotent_for_retained_positions(monkeypatch):
    conn = _Connection()
    positions = [
        {"position_id": 1, "policy": "value_quality", "ticker": "A"},
        {"position_id": 2, "policy": "value_quality", "ticker": "B"},
    ]
    monkeypatch.setattr(strategy_paper, "open_positions", lambda c: positions)
    monkeypatch.setattr(strategy_paper, "_adj_close", lambda *args: 100)

    result = strategy_paper.sync(conn, _screen(), "screen_1")

    assert result["opened"] == [] and result["closed"] == []
    assert len(result["retained"]) == 2
    assert all("UPDATE sm_strategy_paper_positions SET target_weight" in sql
               for sql, _ in conn.calls)


def test_strategy_paper_refuses_non_paper_screen():
    try:
        strategy_paper.sync(_Connection(), {"status": "STALE"}, "screen_1")
    except ValueError as exc:
        assert "PAPER_ONLY" in str(exc)
    else:
        raise AssertionError("stale screen must not alter the paper ledger")


def test_strategy_paper_refuses_empty_ok_screen():
    screen = {"status": "OK", "execution_status": "PAPER_ONLY",
              "policies": {}}
    try:
        strategy_paper.sync(_Connection(), screen, "screen_1")
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty screen must not liquidate the paper ledger")
