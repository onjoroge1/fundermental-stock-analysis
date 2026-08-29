from stock_machine.p1 import _optional_read


class FakeConn:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def test_optional_read_returns_fallback_and_recovers_transaction():
    conn = FakeConn()
    warnings = []

    def boom():
        raise RuntimeError("relation does not exist")

    result = _optional_read(conn, "p1_research_runs", boom, None, warnings)

    assert result is None
    assert conn.rollbacks == 1
    assert warnings == [{
        "component": "p1_research_runs",
        "status": "PENDING",
        "reason": "RuntimeError: relation does not exist",
    }]


def test_optional_read_preserves_success_without_warning():
    conn = FakeConn()
    warnings = []

    result = _optional_read(conn, "macro_series", lambda: {"ok": True}, {}, warnings)

    assert result == {"ok": True}
    assert conn.rollbacks == 0
    assert warnings == []
