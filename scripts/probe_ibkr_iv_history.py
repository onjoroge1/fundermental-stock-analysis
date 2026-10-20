"""How much daily implied-volatility history can IBKR supply? A local, read-only probe.

  python scripts/probe_ibkr_iv_history.py [--port 4001] [--tickers AAPL,MSFT] [--out data/ibkr_iv]

Run it on the machine where TWS or IB Gateway is logged in (enable
"Read-Only API" in its API settings; this script never places or modifies
orders and never handles credentials). For every covered stock and SPY it asks
IBKR for:

  OPTION_IMPLIED_VOLATILITY  IBKR's 30-day at-the-money implied volatility,
                             interpolated from two consecutive expiries
  HISTORICAL_VOLATILITY      IBKR's 30-day historical volatility

first the earliest available date (reqHeadTimestamp), then the daily bars
(reqHistoricalData, 1-day bars). Requests are spaced to stay inside IBKR's
historical-data pacing (about 60 requests per 10 minutes): roughly 4 requests
per symbol x 55 symbols x 10s = about 40 minutes.

It writes, under --out/<run date>/:
  <TICKER>.json   the bars, labelled as IBKR-computed, current vintage
                  (not observed option quotes; expired contracts are not
                  available from IBKR at all)
  coverage.json   per symbol and series: earliest date, bars, sessions
  coverage.md     covered, gaps; shareable, contains no account details

Nothing is written to the database. A finished symbol is skipped on a rerun
unless --force is given, so the probe can be resumed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SERIES = ("OPTION_IMPLIED_VOLATILITY", "HISTORICAL_VOLATILITY")
BENIGN = {2104, 2106, 2107, 2108, 2158, 2119, 2100}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
PROVENANCE = ("IBKR TWS reqHistoricalData, 1-day bars; vendor-computed volatility (30-day ATM implied, or 30-day "
              "historical), current vintage; not observed option quotes")


class ProbeError(RuntimeError):
    pass


def parse_bar_date(raw) -> str:
    text = str(raw).split()[0]
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"


def parse_head(raw: str | None) -> str | None:
    """reqHeadTimestamp gives 'YYYYMMDD' or 'YYYYMMDD-HH:MM:SS' (or with a space)."""
    return parse_bar_date(str(raw).replace("-", " ", 1)) if raw else None


class TWSSession:
    """One read-only connection for the whole probe."""

    def __init__(self, host: str, port: int, client_id: int, timeout: float, spacing: float):
        try:
            from ibapi.client import EClient
            from ibapi.wrapper import EWrapper
        except ModuleNotFoundError:
            raise ProbeError("IBKR's Python API (ibapi) is not installed in this Python. Use the environment that "
                             "has it, e.g. ~/.venvs/stock-analyzer-2026/bin/python, or install IBKR's own TWS API "
                             "release (PyPI's ibapi is an old 9.x version)") from None

        class _Wrapper(EWrapper):
            def __init__(self):
                EWrapper.__init__(self)
                self.ready = threading.Event()
                self.done, self.bars, self.heads, self.errors = {}, {}, {}, {}
                self.closed, self.farm_broken = False, False

            def nextValidId(self, orderId):
                self.ready.set()

            def connectionClosed(self):
                self.closed = True
                for event in list(self.done.values()):
                    event.set()

            def error(self, reqId, errorTime, errorCode=None, errorString="", advancedOrderRejectJson=""):
                code, text = errorCode, errorString
                if code is None or isinstance(code, str):
                    code, text = errorTime, (errorCode or "")
                if code in BENIGN:
                    return
                if code in (1100, 2110, 2103, 2105, 2157):     # connectivity or a data farm lost
                    self.farm_broken = True
                elif code in (1101, 1102):                     # restored
                    self.farm_broken = False
                self.errors.setdefault(reqId, []).append((code, str(text)[:200]))
                if reqId in self.done:
                    self.done[reqId].set()

            def headTimestamp(self, reqId, headTimestamp):
                self.heads[reqId] = headTimestamp
                if reqId in self.done:
                    self.done[reqId].set()

            def historicalData(self, reqId, bar):
                self.bars.setdefault(reqId, []).append(bar)

            def historicalDataEnd(self, reqId, start, end):
                if reqId in self.done:
                    self.done[reqId].set()

        self.wrapper = _Wrapper()
        self.client = EClient(self.wrapper)
        self.timeout, self.spacing, self.next_id, self.last = timeout, spacing, 7000, 0.0
        self.client.connect(host, port, client_id)
        threading.Thread(target=self.client.run, daemon=True).start()
        if not self.wrapper.ready.wait(30) or self.client.serverVersion() is None:
            self.close()
            raise ProbeError(f"no TWS/Gateway handshake at {host}:{port}; is it running, logged in, with the API on?")

    def _contract(self, symbol: str):
        from ibapi.contract import Contract
        c = Contract()
        c.symbol, c.secType, c.currency, c.exchange = symbol, "STK", "USD", "SMART"
        return c

    def _check_connected(self):
        if self.wrapper.closed or not self.client.isConnected():
            raise ProbeError("lost the TWS/Gateway connection; rerun the same command to resume")

    def _request(self, send, cancel=None) -> tuple[int, threading.Event]:
        self._check_connected()
        restored_by = time.monotonic() + 300
        while self.wrapper.farm_broken and time.monotonic() < restored_by:   # wait for IBKR to reconnect
            time.sleep(5)
        if self.wrapper.farm_broken:
            raise ProbeError("IBKR's data connection is broken (see the Gateway window); rerun to resume")
        wait = self.spacing - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.next_id += 1
        event = threading.Event()
        self.wrapper.done[self.next_id] = event
        send(self.next_id)
        self.last = time.monotonic()
        if not event.wait(self.timeout):
            self.wrapper.errors.setdefault(self.next_id, []).append(("TIMEOUT", f"no answer in {self.timeout:.0f}s"))
            if cancel:   # an unanswered request must not hold up the ones after it
                try:
                    cancel(self.next_id)
                except Exception:
                    pass
        self._check_connected()
        return self.next_id, event

    def head_timestamp(self, symbol: str, what: str) -> tuple[str | None, list]:
        rid, _ = self._request(lambda r: self.client.reqHeadTimeStamp(r, self._contract(symbol), what, 1, 1))
        try:
            self.client.cancelHeadTimeStamp(rid)
        except Exception:
            pass
        return parse_head(self.wrapper.heads.get(rid)), self.wrapper.errors.get(rid, [])

    def daily_bars(self, symbol: str, what: str, duration: str) -> tuple[list[dict], list]:
        rid, _ = self._request(lambda r: self.client.reqHistoricalData(
            r, self._contract(symbol), "", duration, "1 day", what, 1, 1, False, []),
            cancel=self.client.cancelHistoricalData)
        rows = [{"date": parse_bar_date(b.date), "open": float(b.open), "high": float(b.high),
                 "low": float(b.low), "close": float(b.close)} for b in self.wrapper.bars.get(rid, [])]
        return sorted(rows, key=lambda r: r["date"]), self.wrapper.errors.get(rid, [])

    def close(self):
        try:
            self.client.disconnect()
        except Exception:
            pass


def coverage(rows: list[dict], head: str | None, sessions_between) -> dict:
    """Earliest date, bars, and the share of exchange sessions covered since the first bar."""
    if not rows:
        return {"head_timestamp": head, "first": None, "last": None, "bars": 0, "years": 0.0,
                "session_coverage": None, "longest_gap_sessions": None}
    dates = [r["date"] for r in rows]
    expected = sessions_between(dates[0], dates[-1])
    have = set(dates)
    longest, run = 0, 0
    for s in expected:
        run = 0 if s in have else run + 1
        longest = max(longest, run)
    positive = [r for r in rows if r["close"] > 0]
    return {"head_timestamp": head, "first": dates[0], "last": dates[-1], "bars": len(rows),
            "years": round((datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days / 365.25, 2),
            "session_coverage": round(len(have & set(expected)) / len(expected), 4) if expected else None,
            "longest_gap_sessions": longest, "non_positive_bars": len(rows) - len(positive),
            "median_close": sorted(r["close"] for r in positive)[len(positive) // 2] if positive else None}


def probe_symbol(session, symbol: str, duration: str, sessions_between) -> dict:
    out = {"ticker": symbol, "retrieved_at": datetime.now(timezone.utc).isoformat(), "provenance": PROVENANCE,
           "duration": duration, "series": {}}
    for what in SERIES:
        head, head_errors = session.head_timestamp(symbol, what)
        rows, errors = session.daily_bars(symbol, what, duration)
        out["series"][what] = {"coverage": coverage(rows, head, sessions_between),
                               "errors": [list(e) for e in head_errors + errors], "rows": rows}
    return out


def markdown(summary: dict) -> str:
    lines = [f"# IBKR volatility history probe, {summary['run_date']}", "",
             f"{summary['symbols']} symbols; duration requested {summary['duration']}. {PROVENANCE}.", "",
             "| Symbol | Implied vol from | IBKR earliest | Bars | Session coverage | Longest gap | Historical vol from | Errors |",
             "|---|---|---|---|---|---|---|---|"]
    for t, row in sorted(summary["coverage"].items()):
        iv, hv = row.get("OPTION_IMPLIED_VOLATILITY") or {}, row.get("HISTORICAL_VOLATILITY") or {}
        lines.append(f"| {t} | {iv.get('first') or '—'} | {iv.get('head_timestamp') or '—'} | {iv.get('bars', 0)} | "
                     f"{iv.get('session_coverage') or '—'} | "
                     f"{iv.get('longest_gap_sessions') if iv.get('longest_gap_sessions') is not None else '—'} | "
                     f"{hv.get('first') or '—'} | {row.get('errors') or ''} |")
    firsts = sorted(r["OPTION_IMPLIED_VOLATILITY"]["first"] for r in summary["coverage"].values()
                    if (r.get("OPTION_IMPLIED_VOLATILITY") or {}).get("first"))
    lines += ["", f"Implied-vol history: {len(firsts)} of {summary['symbols']} symbols returned bars"
              + (f"; median first date {firsts[len(firsts) // 2]}, latest first date {firsts[-1]}." if firsts else ".")]
    return "\n".join(lines) + "\n"


def complete(data: dict, duration: str) -> bool:
    """A saved symbol may be kept on resume: same duration, bars in both series, nothing timed out."""
    return (data.get("duration", duration) == duration
            and all(data["series"].get(w, {}).get("coverage", {}).get("bars") for w in SERIES)
            and not any(e and e[0] == "TIMEOUT" for w in SERIES for e in data["series"].get(w, {}).get("errors", [])))


def run(session, symbols: list[str], out_dir: Path, *, duration: str, force: bool, sessions_between,
        log=print) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"run_date": out_dir.name, "duration": duration, "symbols": len(symbols), "coverage": {}}
    for k, symbol in enumerate(symbols, 1):
        path = out_dir / f"{symbol}.json"
        saved = json.loads(path.read_text()) if path.exists() and not force else None
        if saved and complete(saved, duration):
            data = saved
            log(f"[{k}/{len(symbols)}] {symbol}: already probed, kept")
        else:
            data = probe_symbol(session, symbol, duration, sessions_between)
            path.write_text(json.dumps(data, indent=1))
            iv = data["series"]["OPTION_IMPLIED_VOLATILITY"]["coverage"]
            log(f"[{k}/{len(symbols)}] {symbol}: implied vol from {iv['first']} ({iv['bars']} bars)")
        summary["coverage"][symbol] = {
            **{what: data["series"][what]["coverage"] for what in SERIES},
            "errors": "; ".join(f"{what[:2]} {c}: {m}" for what in SERIES for c, m in data["series"][what]["errors"]),
        }
    (out_dir / "coverage.json").write_text(json.dumps(summary, indent=1, sort_keys=True))
    (out_dir / "coverage.md").write_text(markdown(summary))
    return summary


def main() -> int:
    from stock_machine.agents.contracts import AGENT_UNIVERSE
    from stock_machine.market_calendar import session_dates

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("IBKR_TWS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("IBKR_TWS_PORT", "4001")),
                        help="4001 IB Gateway live, 4002 Gateway paper, 7496 TWS live, 7497 TWS paper")
    parser.add_argument("--client-id", type=int, default=int(os.environ.get("IBKR_TWS_PROBE_CLIENT_ID", "29")))
    parser.add_argument("--tickers", help="comma-separated; default the covered universe and SPY")
    parser.add_argument("--duration", default="21 Y",
                        help="daily bars; IBKR's implied-vol history starts 2006-01-06 (21 Y verified for MSFT)")
    parser.add_argument("--spacing", type=float, default=11.0,
                        help="seconds between requests (IBKR allows about 60 historical requests per 10 minutes)")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out", default="data/ibkr_iv")
    parser.add_argument("--force", action="store_true", help="probe symbols already written")
    parser.add_argument("--allow-remote", action="store_true", help="connect to a non-local TWS host")
    args = parser.parse_args()
    if args.host not in LOCAL_HOSTS and not args.allow_remote:
        raise SystemExit(f"refusing non-local host {args.host}; pass --allow-remote if that is intended")
    symbols = ([t.strip().upper() for t in args.tickers.split(",")] if args.tickers
               else sorted(set(AGENT_UNIVERSE) | {"SPY"}))
    out_dir = Path(args.out) / datetime.now().date().isoformat()   # local date: an evening rerun resumes
    try:
        session = TWSSession(args.host, args.port, args.client_id, args.timeout, args.spacing)
    except ProbeError as exc:
        raise SystemExit(str(exc)) from None
    try:
        summary = run(session, symbols, out_dir, duration=args.duration, force=args.force,
                      sessions_between=session_dates)
    finally:
        session.close()
    print(markdown(summary))
    print(f"wrote {out_dir}/coverage.md and coverage.json (share these; they contain no account details)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
