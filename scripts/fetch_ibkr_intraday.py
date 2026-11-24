"""Fetch 1-minute regular-session bars from IBKR to local files, for the intraday replay.

  ~/.venvs/stock-analyzer-2026/bin/python scripts/fetch_ibkr_intraday.py --months 2023-06 [--tickers AAPL,SPY]
      [--window full|first-hour] [--spacing 2] [--out data/ibkr_intraday/v1]

Runs where TWS / IB Gateway is logged in. Read-only market data: no orders, accounts or credentials; local host
only. TRADES 1-minute bars, regular hours, epoch timestamps: the full window asks for each week's sessions of the
month in one request (IBKR answers a week in about the time it takes for a day), asks again for any session that
came back short on its own, and asks day by day for a week IBKR will not answer whole; the first-hour window asks per session. The bars are split-adjusted, not
dividend-adjusted (the replay takes the dividend factor from the daily ADJUSTED_LAST files). Files stay local:
IBKR's terms restrict redistributing its data.

Layout, split by stock and month so the fetch resumes and space stays bounded:
  <out>/<TICKER>/<YYYY-MM>.csv.gz   session,minute,open,high,low,close,volume,wap,count (minute = HH:MM ET)
  <out>/<TICKER>/<YYYY-MM>.json     manifest: window, sessions, bars, empty or failed sessions, errors, timing
  <out>/<TICKER>/<YYYY-MM>.partial.json   progress while a month is being fetched (removed when complete)
A session is asked for only once it has closed (SETTLE after the closing bell). A month whose last session has not
closed yet is written with what there is and its manifest lists the rest under "open_sessions"; rerunning asks for
those of them that have closed since. A month is final once every session has an answer, and rerunning skips final
months. Months from 2024-01 on (the
replay's hold-out) are refused unless --allow-holdout: they are fetched only after its protocol is registered.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import io
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("probe_ibkr_iv_history", ROOT / "scripts" / "probe_ibkr_iv_history.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

ET = ZoneInfo("America/New_York")
HOLDOUT_FROM = "2024-01"
WINDOWS = {"full": ("16:00:00", "1 D"), "first-hour": ("10:30:00", "3600 S")}
COLUMNS = ("session", "minute", "open", "high", "low", "close", "volume", "wap", "count")
SETTLE = timedelta(minutes=20)        # after the closing bell, before a session's bars are asked for
PROVENANCE = "IBKR TWS reqHistoricalData, TRADES, 1 min, useRTH=1, formatDate=2 (split-adjusted, not dividend-adjusted)"


class IntradaySession(probe.TWSSession):
    def _bars(self, symbol: str, end: str, duration: str, keep: set[str]) -> tuple[dict, list]:
        rid, _ = self._request(lambda r: self.client.reqHistoricalData(
            r, self._contract(symbol), end, duration, "1 min", "TRADES", 1, 2, False, []),
            cancel=self.client.cancelHistoricalData)
        raw = self.wrapper.bars.pop(rid, [])
        errors = self.wrapper.errors.pop(rid, [])
        self.wrapper.done.pop(rid, None)
        out = {}
        for b in raw:
            stamp = datetime.fromtimestamp(int(b.date), tz=timezone.utc).astimezone(ET)
            session = stamp.date().isoformat()
            if session not in keep:
                continue                       # never keep a bar from a session not asked for
            out.setdefault(session, []).append({
                "session": session, "minute": stamp.strftime("%H:%M"), "open": float(b.open), "high": float(b.high),
                "low": float(b.low), "close": float(b.close),
                "volume": float(b.volume) if b.volume is not None and float(b.volume) >= 0 else 0.0,
                "wap": float(b.wap) if b.wap is not None else None, "count": int(b.barCount or 0)})
        return {k: sorted(v, key=lambda r: r["minute"]) for k, v in out.items()}, errors

    def minute_bars(self, symbol: str, session: str, window: str) -> tuple[list[dict], list]:
        clock, duration = WINDOWS[window]
        got, errors = self._bars(symbol, f"{session.replace('-', '')} {clock} US/Eastern", duration, {session})
        return got.get(session, []), errors

    def week_bars(self, symbol: str, sessions: list[str]) -> tuple[dict, list]:
        """Full-session bars for one week's sessions in one request: {session: rows}. The duration spans the
        calendar days from the first session to the last, so it covers them whether IBKR counts calendar or
        trading days; anything outside them is dropped."""
        days = (date.fromisoformat(sessions[-1]) - date.fromisoformat(sessions[0])).days + 1
        return self._bars(symbol, f"{sessions[-1].replace('-', '')} 16:00:00 US/Eastern", f"{days} D", set(sessions))


def month_schedule(month: str, window: str = "full") -> dict:
    """session -> the number of 1-minute bars a complete session has (390, 210 on an early close; 60 for the
    first-hour window), from the XNYS calendar."""
    from stock_machine.market_calendar import _calendar, session_dates
    year, m = map(int, month.split("-"))
    last = date(year + (m == 12), 1 if m == 12 else m + 1, 1).toordinal() - 1
    cal = _calendar(year, year)
    out = {}
    for s in session_dates(f"{month}-01", date.fromordinal(last).isoformat()):
        minutes = int((cal.session_close(s) - cal.session_open(s)).total_seconds() // 60)
        out[s] = min(minutes, 60) if window == "first-hour" else minutes
    return out


def session_closes(sessions) -> dict:
    """session -> the time its bars are complete (closing bell plus SETTLE, UTC), from the XNYS calendar."""
    from stock_machine.market_calendar import _calendar
    sessions = sorted(sessions)
    if not sessions:
        return {}
    cal = _calendar(int(sessions[0][:4]), int(sessions[-1][:4]))
    return {s: cal.session_close(s).to_pydatetime().astimezone(timezone.utc) + SETTLE for s in sessions}


def _stamp(text) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def asked_too_early(manifest: dict, closes: dict) -> list[str]:
    """Sessions of a written month that have no trustworthy answer yet: the ones its manifest lists as open, or,
    for a manifest written before that list existed, every session that closed after the month was last
    fetched or checked (IBKR's "no data" for a session still to come was then recorded as an answer)."""
    if "open_sessions" in manifest:
        return [s for s in manifest["open_sessions"] if s in closes]
    stamps = [x for x in (_stamp(manifest.get(k)) for k in ("retrieved_at", "checked_complete_at")) if x]
    if not stamps:
        return sorted(closes)
    return sorted(s for s, at in closes.items() if at > max(stamps))


def first_daily_bar(symbol: str, prices_dir: Path | None) -> str | None:
    path = prices_dir / f"{symbol}.json" if prices_dir else None
    if path and path.exists():
        return json.loads(path.read_text()).get("first")
    return None


def _failed(got, errors) -> bool:
    """A timeout means the answer may be cut short even when rows came back; pacing means none came."""
    timeout = any(str(code) == "TIMEOUT" for code, _ in errors)
    return timeout or (not got and any("pacing" in str(text).lower() for _, text in errors))


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def fetch_month(session_api, symbol: str, month: str, window: str, out: Path, *, listed_from: str | None,
                log=print, backoff: float = 90.0, allow_holdout: bool = False, schedule: dict | None = None,
                now: datetime | None = None) -> dict:
    if month >= HOLDOUT_FROM and not allow_holdout:
        raise SystemExit(f"{month} is in the replay's hold-out: fetched only after its protocol is registered")
    folder = out / symbol
    folder.mkdir(parents=True, exist_ok=True)
    final, partial = folder / f"{month}.json", folder / f"{month}.partial.json"
    expected = schedule if schedule is not None else month_schedule(month, window)
    now = now or datetime.now(timezone.utc)
    closes = session_closes(expected)
    still_open = sorted(s for s in expected if closes[s] > now)
    written = json.loads(final.read_text()) if final.exists() else None
    kept = {}                                  # rows already on file for a session that is asked for again
    if written is not None:
        early = set(asked_too_early(written, closes))
        if not early - set(still_open):
            return written
        if written.get("window", window) != window:
            raise SystemExit(f"{symbol} {month}: the month was fetched with window {written['window']}")
        data = folder / f"{month}.csv.gz"
        if not data.exists():
            raise SystemExit(f"{symbol} {month}: the manifest is there but {data.name} is not")
        # A written month is reopened from its own files, in memory: until the new answers are written the files
        # stay as they are, readable, and an interrupted run starts again from them. Progress left beside a
        # written month is from a run that did not finish and is not trusted.
        partial.unlink(missing_ok=True)
        on_file = {}
        with gzip.open(data, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                on_file.setdefault(r["session"], []).append(r)
        kept = {s: rows for s, rows in on_file.items() if s in early}
        progress = {"sessions": {}}
        for s in expected:
            if s in early:
                continue
            if listed_from and s < listed_from and not on_file.get(s):
                progress["sessions"][s] = {"status": "BEFORE_FIRST_DAILY_BAR", "rows": []}
            else:
                progress["sessions"][s] = {"status": "OK" if on_file.get(s) else "NO_BARS", "rows": on_file.get(s, []),
                                           "errors": (written.get("errors") or {}).get(s) or []}
    else:
        progress = json.loads(partial.read_text()) if partial.exists() else {"sessions": {}}
        if progress.get("window", window) != window:
            raise SystemExit(f"{symbol} {month}: partial progress was fetched with window {progress['window']}")
    progress["window"] = window
    started = time.monotonic()

    def save():
        if written is None:
            _write_atomic(partial, json.dumps(progress).encode())

    def ask(call, label):
        got, errors = call()
        if _failed(got, errors):
            log(f"  {symbol} {label}: {errors[-1]}; waiting {backoff:.0f}s and retrying once")
            time.sleep(backoff)
            got, errors = call()
        if _failed(got, errors):
            save()
            raise probe.ProbeError(f"{symbol} {label}: IBKR keeps throttling or not answering ({errors[-1]}); "
                                   "rerun the same command later to resume")
        return got, errors

    pending = []
    for s in expected:
        if s in progress["sessions"] or s in still_open:
            continue
        if listed_from and s < listed_from and not kept.get(s):
            progress["sessions"][s] = {"status": "BEFORE_FIRST_DAILY_BAR", "rows": []}
        else:
            pending.append(s)
    weeks = {}
    for s in pending:
        weeks.setdefault(date.fromisoformat(s).isocalendar()[:2] if window == "full" else s, []).append(s)
    for week in weeks.values():
        got, errors = {}, []
        if len(week) > 1:
            try:
                got, errors = ask(lambda w=week: session_api.week_bars(symbol, w), f"{week[0]}..{week[-1]}")
            except probe.ProbeError:              # IBKR will not answer this week whole: ask day by day
                log(f"  {symbol} {week[0]}..{week[-1]}: asking for each session alone")
        for s in week:
            rows, errs = got.get(s, []), errors
            if len(rows) < expected[s]:        # a single session, or one the week returned short: ask for it alone
                alone, alone_errs = ask(lambda d=s: session_api.minute_bars(symbol, d, window), s)
                if len(alone) >= len(rows):
                    rows, errs = alone, alone_errs
            if len(kept.get(s, [])) > len(rows):   # never replace a longer answer already on file with a shorter one
                rows, errs = kept[s], (written.get("errors") or {}).get(s) or []
            progress["sessions"][s] = {"status": "OK" if rows else "NO_BARS", "rows": rows,
                                       "errors": [list(e) for e in errs]}
        save()
    rows = [r for s in sorted(progress["sessions"]) for r in progress["sessions"][s]["rows"]]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    _write_atomic(folder / f"{month}.csv.gz", gzip.compress(buffer.getvalue().encode("utf-8"), compresslevel=9, mtime=0))
    statuses = {s: v["status"] for s, v in progress["sessions"].items()}
    manifest = {"ticker": symbol, "month": month, "window": window, "provenance": PROVENANCE,
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "sessions": len(statuses),
                "bars": len(rows), "by_status": {k: sum(v == k for v in statuses.values()) for k in set(statuses.values())},
                "empty_sessions": sorted(s for s, v in statuses.items() if v == "NO_BARS"),
                "open_sessions": still_open,
                "short_sessions": {s: [len(v["rows"]), expected[s]] for s, v in sorted(progress["sessions"].items())
                                   if v["status"] == "OK" and len(v["rows"]) < expected[s]},
                "errors": {s: v.get("errors") for s, v in progress["sessions"].items() if v.get("errors")},
                "seconds": round(time.monotonic() - started, 1)}
    if written is not None and written.get("repaired"):
        manifest["repaired"] = written["repaired"]             # what an earlier repair recorded stays
    _write_atomic(final, json.dumps(manifest, indent=1).encode())
    partial.unlink(missing_ok=True)
    return manifest


def repair_month(session_api, symbol: str, month: str, window: str, out: Path, *, log=print, backoff: float = 90.0,
                 allow_holdout: bool = False, schedule: dict | None = None,
                 now: datetime | None = None) -> dict | None:
    """For a written month, ask again, alone, for every closed session with fewer bars than a complete one
    (fetched before short answers were re-asked, or genuinely thin); keep the longer answer. A session that has
    not closed is left to the fetch. Returns the updated manifest, or None when the month is not written."""
    if month >= HOLDOUT_FROM and not allow_holdout:
        raise SystemExit(f"{month} is in the replay's hold-out: fetched only after its protocol is registered")
    folder = out / symbol
    final, data = folder / f"{month}.json", folder / f"{month}.csv.gz"
    if not final.exists() or (folder / f"{month}.partial.json").exists():
        return None
    manifest = json.loads(final.read_text())
    expected = schedule if schedule is not None else month_schedule(month, window)
    now = now or datetime.now(timezone.utc)
    closes = session_closes(expected)
    by_session = {}
    for r in csv.DictReader(io.StringIO(gzip.decompress(data.read_bytes()).decode("utf-8"))):
        by_session.setdefault(r["session"], []).append(r)
    repaired = []
    for s, n in expected.items():                  # the caller leaves out sessions before the first daily bar
        have = by_session.get(s, [])
        if len(have) >= n or closes[s] > now:
            continue
        rows, errors = session_api.minute_bars(symbol, s, window)
        if _failed(rows, errors):
            log(f"  {symbol} {s}: {errors[-1]}; waiting {backoff:.0f}s and retrying once")
            time.sleep(backoff)
            rows, errors = session_api.minute_bars(symbol, s, window)
        if _failed(rows, errors):
            raise probe.ProbeError(f"{symbol} {s}: IBKR keeps throttling or not answering ({errors[-1]}); "
                                   "rerun the repair later")
        if len(rows) > len(have):
            by_session[s] = rows
            repaired.append(s)
    if repaired:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(r for s in sorted(by_session) for r in by_session[s])
        _write_atomic(data, gzip.compress(buffer.getvalue().encode("utf-8"), compresslevel=9, mtime=0))
    manifest["bars"] = sum(len(v) for v in by_session.values())
    manifest["short_sessions"] = {s: [len(by_session.get(s, [])), n] for s, n in expected.items()
                                  if by_session.get(s) and len(by_session[s]) < n}
    manifest["repaired"] = sorted(set(manifest.get("repaired", [])) | set(repaired))
    manifest["checked_complete_at"] = datetime.now(timezone.utc).isoformat()
    _write_atomic(final, json.dumps(manifest, indent=1).encode())
    return manifest


def months_between(first: str, last: str) -> list[str]:
    out, (year, m) = [], map(int, first.split("-"))
    while f"{year:04d}-{m:02d}" <= last:
        out.append(f"{year:04d}-{m:02d}")
        year, m = (year + 1, 1) if m == 12 else (year, m + 1)
    return out


def main() -> int:
    from stock_machine.agents.contracts import AGENT_UNIVERSE
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("IBKR_TWS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("IBKR_TWS_PORT", "4001")))
    parser.add_argument("--client-id", type=int, default=31)
    parser.add_argument("--tickers")
    parser.add_argument("--months", required=True, help="YYYY-MM or YYYY-MM:YYYY-MM")
    parser.add_argument("--window", choices=sorted(WINDOWS), default="full")
    parser.add_argument("--out", default="data/ibkr_intraday/v1")
    parser.add_argument("--prices-dir", default="data/ibkr_prices/2026-10-20",
                        help="daily files; sessions before a symbol's first daily bar are skipped")
    parser.add_argument("--spacing", type=float, default=10.0)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--allow-holdout", action="store_true")
    parser.add_argument("--repair", action="store_true",
                        help="re-ask, session by session, for short sessions in finished months")
    args = parser.parse_args()
    if args.host not in probe.LOCAL_HOSTS:
        raise SystemExit(f"refusing non-local host {args.host}")
    first, _, last = args.months.partition(":")
    months = months_between(first, last or first)
    out = Path(args.out)
    if not out.resolve().is_relative_to((ROOT / "data").resolve()):
        raise SystemExit(f"refusing --out {out}: IBKR files stay under data/ (gitignored)")
    holdout = [m for m in months if m >= HOLDOUT_FROM]
    if holdout and not args.allow_holdout:
        raise SystemExit(f"months from {HOLDOUT_FROM} on are the replay's hold-out: fetched only after its protocol "
                         "is registered (pass --allow-holdout then)")
    if holdout and (len(holdout) != len(months) or not out.name.endswith("-holdout")):
        raise SystemExit("hold-out months are fetched alone, into their own folder ending in -holdout")
    symbols = ([t.strip().upper() for t in args.tickers.split(",")] if args.tickers
               else sorted(set(AGENT_UNIVERSE) | {"SPY"}))
    prices_dir = Path(args.prices_dir)
    try:
        api = IntradaySession(args.host, args.port, args.client_id, args.timeout, args.spacing)
    except probe.ProbeError as exc:
        raise SystemExit(str(exc)) from None
    t0, done = time.monotonic(), 0
    try:
        for month in months:
            schedule = month_schedule(month, args.window)
            for symbol in symbols:
                if args.repair:
                    first_bar = first_daily_bar(symbol, prices_dir) or ""
                    manifest = repair_month(api, symbol, month, args.window, out, allow_holdout=args.allow_holdout,
                                            schedule={s: n for s, n in schedule.items() if s >= first_bar})
                    done += 1
                    if manifest is not None:
                        print(f"[{done}/{len(months) * len(symbols)}] {symbol} {month}: repaired "
                              f"{len(manifest['repaired'])}, short {len(manifest['short_sessions'])}", flush=True)
                    continue
                manifest = fetch_month(api, symbol, month, args.window, out, schedule=schedule,
                                       listed_from=first_daily_bar(symbol, prices_dir), allow_holdout=args.allow_holdout)
                done += 1
                print(f"[{done}/{len(months) * len(symbols)}] {symbol} {month}: {manifest['bars']} bars, "
                      f"{manifest['by_status']}, {len(manifest.get('open_sessions', []))} sessions not closed yet, "
                      f"{manifest['seconds']}s", flush=True)
    except probe.ProbeError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    finally:
        api.close()
    print(f"done in {time.monotonic() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
