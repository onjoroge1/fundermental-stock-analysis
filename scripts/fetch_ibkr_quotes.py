"""Fetch historical bid/ask (and 1-second trade) bars from IBKR for the cost study (fsa-research/costs/COSTS_SPEC.md).

  ~/.venvs/stock-analyzer-2026/bin/python scripts/fetch_ibkr_quotes.py --plan ~/code/fsa-research/costs/work/plan_S.csv
      [--probe 4] [--limit N] [--port 4001] [--out data/ibkr_quotes/v1]

Runs where TWS / IB Gateway is logged in (keep "Read-Only API" on). Read-only market data: no orders, accounts or
credentials; local host only. The plan is a CSV of requests (id, part, ticker, session, end, duration, bar, what, tag),
built by costs/make_plan.py. Each request is IBKR reqHistoricalData, regular hours, epoch timestamps:
  BID_ASK  open = time-average bid, high = max ask, low = min bid, close = time-average ask (IBKR's definition)
  TRADES   open, high, low, close, volume, wap, count
Files stay local under data/ (gitignored): IBKR's terms restrict redistributing its data.

  <out>/<part>/<TICKER>/<session>_<what>_<1min|1s>.csv.gz   time (HH:MM:SS ET) + the fields above
  <out>/fetch_log.jsonl                                      one line per answered request (status, bars, errors)
A request with a file, or answered "no data", is skipped on a rerun, so the fetch resumes. Throttling is retried
once after a wait; if IBKR keeps throttling the run stops and the same command resumes it.

Pacing (IBKR docs): for bars of 30 s or less, at most 60 requests in any 10 minutes, BID_ASK counted twice, so this
script keeps a 10-minute budget of 55; minute bars are only spaced (--spacing per unit). Refused: any session in
the 2024-01..2026-08 hold-out (no override: a cost study never needs it), sessions not yet complete, plans with
unknown fields, a non-local host, and an output folder outside data/.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import io
import json
import os
import re
import sys
import time
from collections import deque
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_spec = importlib.util.spec_from_file_location("probe_ibkr_iv_history", ROOT / "scripts" / "probe_ibkr_iv_history.py")
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

ET = ZoneInfo("America/New_York")
HOLDOUT = ("2024-01-01", "2026-08-31")
WHATS = {"BID_ASK": ("bid_avg", "ask_max", "bid_min", "ask_avg"),
         "TRADES": ("open", "high", "low", "close", "volume", "wap", "count")}
BARS = {"1 min": "1min", "1 secs": "1s"}
WEIGHT = {"BID_ASK": 2, "TRADES": 1}
SMALL_BUDGET, SMALL_WINDOW = 55, 600.0
PLAN_COLS = ("id", "part", "ticker", "session", "end", "duration", "bar", "what", "tag")
DATE_RE, CLOCK_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$"), re.compile(r"^\d{2}:\d{2}:\d{2}$")
DUR_RE, TICKER_RE = re.compile(r"^\d+ [SD]$"), re.compile(r"^[A-Z][A-Z.]{0,5}$")
PROVENANCE = "IBKR TWS reqHistoricalData, useRTH=1, formatDate=2; BID_ASK = time-average bid/ask, max ask, min bid"


def read_plan(path: Path, today: str | None = None) -> list[dict]:
    today = today or datetime.now(ET).date().isoformat()
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != PLAN_COLS:
            raise SystemExit(f"plan columns must be {PLAN_COLS}")
        rows = list(reader)
    problems = []
    for n, r in enumerate(rows, 2):
        if not (DATE_RE.match(r["session"]) and CLOCK_RE.match(r["end"]) and DUR_RE.match(r["duration"])
                and TICKER_RE.match(r["ticker"]) and r["what"] in WHATS and r["bar"] in BARS
                and re.match(r"^[A-Za-z0-9_.-]+$", r["id"]) and re.match(r"^[A-Za-z0-9]+$", r["part"])):
            problems.append(f"line {n}: bad field in {r}")
        elif HOLDOUT[0] <= r["session"] <= HOLDOUT[1]:
            problems.append(f"line {n}: {r['session']} is in the {HOLDOUT[0]}..{HOLDOUT[1]} hold-out")
        elif r["session"] >= today:
            problems.append(f"line {n}: {r['session']} is not a completed session")
    if len({r["id"] for r in rows}) != len(rows):
        problems.append("duplicate ids")
    if problems:
        raise SystemExit("plan refused:\n  " + "\n  ".join(problems[:20]))
    return rows


def target(out: Path, r: dict) -> Path:
    return out / r["part"] / r["ticker"] / f"{r['session']}_{r['what']}_{BARS[r['bar']]}.csv.gz"


class Pacer:
    """Waits so small-bar requests stay inside IBKR's 60 per 10 minutes (BID_ASK counted twice), and spaces the rest."""

    def __init__(self, spacing: float, clock=time.monotonic, sleep=time.sleep):
        self.spacing, self.clock, self.sleep, self.small, self.last = spacing, clock, sleep, deque(), None

    def wait(self, weight: int, small: bool) -> None:
        now = self.clock()
        if self.last is not None and now - self.last < self.spacing * weight:
            self.sleep(self.spacing * weight - (now - self.last))
        if small:
            while True:
                now = self.clock()
                while self.small and now - self.small[0][0] >= SMALL_WINDOW:
                    self.small.popleft()
                used = sum(w for _, w in self.small)
                if used + weight <= SMALL_BUDGET:
                    break
                self.sleep(SMALL_WINDOW - (now - self.small[0][0]) + 0.5)
            self.small.append((self.clock(), weight))
        self.last = self.clock()


class QuoteSession(probe.TWSSession):
    def bars(self, r: dict) -> tuple[list[dict], list]:
        end = f"{r['session'].replace('-', '')} {r['end']} US/Eastern"
        rid, _ = self._request(lambda i: self.client.reqHistoricalData(
            i, self._contract(r["ticker"]), end, r["duration"], r["bar"], r["what"], 1, 2, False, []),
            cancel=self.client.cancelHistoricalData)
        raw = self.wrapper.bars.pop(rid, [])
        errors = self.wrapper.errors.pop(rid, [])
        self.wrapper.done.pop(rid, None)
        return parse(raw, r), errors


def parse(raw, r: dict) -> list[dict]:
    out = []
    for b in raw:
        stamp = datetime.fromtimestamp(int(b.date), tz=timezone.utc).astimezone(ET)
        if stamp.date().isoformat() != r["session"]:
            continue                                  # never keep a bar from a session not asked for
        row = {"time": stamp.strftime("%H:%M:%S")}
        if r["what"] == "BID_ASK":
            row.update(bid_avg=float(b.open), ask_max=float(b.high), bid_min=float(b.low), ask_avg=float(b.close))
        else:
            row.update(open=float(b.open), high=float(b.high), low=float(b.low), close=float(b.close),
                       volume=float(b.volume) if b.volume is not None and float(b.volume) >= 0 else 0.0,
                       wap=float(b.wap) if b.wap is not None else None, count=int(b.barCount or 0))
        out.append(row)
    return sorted(out, key=lambda x: x["time"])


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def no_data(errors) -> bool:
    return any(code == 162 and "no data" in str(text).lower() for code, text in errors)


def throttled(rows, errors) -> bool:
    timeout = any(str(code) == "TIMEOUT" for code, _ in errors)
    return timeout or (not rows and any("pacing" in str(text).lower() for _, text in errors))


def done_ids(out: Path) -> set:
    log = out / "fetch_log.jsonl"
    if not log.exists():
        return set()
    return {json.loads(x)["id"] for x in log.read_text().splitlines() if x.strip() and json.loads(x)["status"] == "NO_DATA"}


def fetch(api, rows: list[dict], out: Path, pacer: Pacer, *, log=print, backoff: float = 90.0, sleep=time.sleep) -> dict:
    skip = done_ids(out)
    todo = [r for r in rows if r["id"] not in skip and not target(out, r).exists()]
    counts = {"OK": 0, "NO_DATA": 0, "skipped": len(rows) - len(todo)}
    for n, r in enumerate(todo, 1):
        small = r["bar"] == "1 secs"

        def ask():
            pacer.wait(WEIGHT[r["what"]], small)
            return api.bars(r)
        got, errors = ask()
        if throttled(got, errors):
            log(f"  {r['id']}: {errors[-1]}; waiting {backoff:.0f}s and retrying once")
            sleep(backoff)
            got, errors = ask()
        if throttled(got, errors):
            raise probe.ProbeError(f"{r['id']}: IBKR keeps throttling or not answering ({errors[-1]}); "
                                   "rerun the same command later to resume")
        status = "OK" if got else ("NO_DATA" if no_data(errors) else "FAILED")
        if got:
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=("time",) + WHATS[r["what"]])
            w.writeheader()
            w.writerows(got)
            _write_atomic(target(out, r), gzip.compress(buf.getvalue().encode(), compresslevel=9, mtime=0))
        if status != "FAILED":
            counts[status] += 1
        else:
            counts["FAILED"] = counts.get("FAILED", 0) + 1
        out.mkdir(parents=True, exist_ok=True)
        with open(out / "fetch_log.jsonl", "a") as f:
            f.write(json.dumps({"id": r["id"], "status": status, "bars": len(got), "errors": [list(e) for e in errors],
                                "first": got[0]["time"] if got else None, "last": got[-1]["time"] if got else None,
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "provenance": PROVENANCE}) + "\n")
        log(f"[{n}/{len(todo)}] {r['id']}: {status}, {len(got)} bars" + (f", {errors[-1]}" if errors and not got else ""))
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--host", default=os.environ.get("IBKR_TWS_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("IBKR_TWS_PORT", "4001")))
    ap.add_argument("--client-id", type=int, default=33)
    ap.add_argument("--out", default="data/ibkr_quotes/v1")
    ap.add_argument("--spacing", type=float, default=3.0, help="seconds between requests, per pacing unit")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--probe", type=int, default=0, help="fetch only the first N pending requests and print them")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    if a.host not in probe.LOCAL_HOSTS:
        raise SystemExit(f"refusing non-local host {a.host}")
    out = Path(a.out)
    if not out.resolve().is_relative_to((ROOT / "data").resolve()):
        raise SystemExit(f"refusing --out {out}: IBKR files stay under data/ (gitignored)")
    rows = read_plan(Path(a.plan).expanduser())
    n = a.probe or a.limit
    if n:
        skip = done_ids(out)
        rows = [r for r in rows if r["id"] not in skip and not target(out, r).exists()][:n]
    try:
        api = QuoteSession(a.host, a.port, a.client_id, a.timeout, 0.0)
    except probe.ProbeError as exc:
        raise SystemExit(str(exc)) from None
    t0 = time.monotonic()
    try:
        counts = fetch(api, rows, out, Pacer(a.spacing))
    except probe.ProbeError as exc:
        print(f"stopped: {exc}", file=sys.stderr)
        return 2
    finally:
        api.close()
    print(f"done in {time.monotonic() - t0:.0f}s: {counts}")
    if a.probe:
        for r in rows:
            p = target(out, r)
            if p.exists():
                lines = gzip.decompress(p.read_bytes()).decode().splitlines()
                print(f"{r['id']}: {len(lines) - 1} bars; {lines[0]}\n  {lines[1]}\n  {lines[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
