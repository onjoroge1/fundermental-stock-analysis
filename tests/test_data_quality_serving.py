from stock_machine import webapp


class _Connection:
    closed = False

    def close(self):
        self.closed = True


def test_data_quality_endpoint_is_read_only(monkeypatch):
    conn = _Connection()
    monkeypatch.setattr(webapp.db, "connect", lambda: conn)
    monkeypatch.setattr(
        webapp.db, "list_companies",
        lambda c: [{"ticker": "AAPL", "legal_name": "Apple"}],
    )
    monkeypatch.setattr(webapp.db, "latest_dataset_snapshots", lambda c: [])

    result = webapp.data_quality_dashboard()

    assert result["summary"]["BLOCKED"] == 1
    assert conn.closed is True
