"""Mirrored local records: results summaries of strategies that run on the owner's machine.

Some strategies cannot run here, because the market data they need is licensed to the owner's machine and
must stay there. Their frozen recorders keep running there. ``scripts/office_export_local_record.py`` turns
a recorder's output into a small summary, and this module checks and stores it.

A summary holds sessions, counts, net figures and hashes. It has no ticker, no price, no bar and no free
text: ``validate`` refuses any field it does not know, so nothing else can be carried in. The frozen file
hashes in the summary must equal the ones the strategy inventory recorded; a changed rule file is refused.

Nothing here trades, admits a strategy or changes a deployment. A mirrored record is a simulation kept
elsewhere and is labelled as such.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from . import config, tasks, tools

SCHEMA = "office.external_record.v1"
MAX_BYTES = 8_000
TOP = ("schema", "strategy_id", "version", "record_name", "rule_hashes", "source", "first_session",
       "through_session", "sessions", "trades", "trade_dates", "figures", "generated_at")
# figure name -> kind. A figure may also be null: "the recorder has no value yet".
FIGURES = {
    "mean_net_bps": "number", "t_session": "number", "max_drawdown_pct": "number",
    "equity_usd": "number", "return_pct": "number", "gross_usd": "number",
    "sessions_not_scored": "count", "stress_sessions": "count", "open_positions": "count",
    "orders": "count", "fills": "count", "missed_orders": "count", "fill_rejections": "count",
    "decision_due": "flag",
}
_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_NAME = re.compile(r"^[A-Za-z0-9._ -]{1,60}$")
_HASH_NAME = re.compile(r"^[A-Za-z0-9._ /-]{1,120}$")
_FILE = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class ExternalRecordError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _need(ok: bool, code: str) -> None:
    if not ok:
        raise ExternalRecordError(code)


def _count(value, top: int) -> bool:
    return type(value) is int and 0 <= value <= top


def _figure(kind: str, value) -> bool:
    if value is None:
        return True
    if kind == "flag":
        return type(value) is bool
    if kind == "count":
        return _count(value, 10_000_000)
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e12


def validate(payload) -> dict:
    """Return the summary unchanged if every field is known and well formed; otherwise raise a code."""
    _need(isinstance(payload, dict), "RECORD_NOT_AN_OBJECT")
    _need(len(json.dumps(payload, default=str)) <= MAX_BYTES, "RECORD_TOO_LARGE")
    _need(set(payload) <= set(TOP), "RECORD_FIELD_NOT_ALLOWED")
    _need(set(payload) == set(TOP), "RECORD_FIELD_MISSING")
    _need(payload["schema"] == SCHEMA, "RECORD_SCHEMA_UNKNOWN")
    for key in ("strategy_id", "version"):
        _need(isinstance(payload[key], str) and bool(_ID.match(payload[key])), "RECORD_STRATEGY_INVALID")
    _need(isinstance(payload["record_name"], str) and bool(_NAME.match(payload["record_name"])),
          "RECORD_NAME_INVALID")
    hashes = payload["rule_hashes"]
    _need(isinstance(hashes, dict) and 1 <= len(hashes) <= 20, "RECORD_RULE_HASHES_INVALID")
    for name, value in hashes.items():
        _need(isinstance(name, str) and bool(_HASH_NAME.match(name)) and isinstance(value, str)
              and bool(_SHA.match(value)), "RECORD_RULE_HASHES_INVALID")
    source = payload["source"]
    _need(isinstance(source, dict) and set(source) == {"file", "sha256"}, "RECORD_SOURCE_INVALID")
    _need(isinstance(source["file"], str) and bool(_FILE.match(source["file"])) and ".." not in source["file"]
          and isinstance(source["sha256"], str) and bool(_SHA.match(source["sha256"])), "RECORD_SOURCE_INVALID")
    try:
        first, through = (date.fromisoformat(payload[k]) for k in ("first_session", "through_session"))
        generated = datetime.fromisoformat(payload["generated_at"])
    except (TypeError, ValueError):
        raise ExternalRecordError("RECORD_DATE_INVALID") from None
    _need(first <= through and generated.tzinfo is not None, "RECORD_DATE_INVALID")
    _need(_count(payload["sessions"], 10_000) and _count(payload["trades"], 10_000_000)
          and _count(payload["trade_dates"], 10_000), "RECORD_COUNT_INVALID")
    _need(payload["trade_dates"] <= min(payload["trades"], payload["sessions"]), "RECORD_COUNT_INVALID")
    figures = payload["figures"]
    _need(isinstance(figures, dict), "RECORD_FIGURES_INVALID")
    _need(set(figures) <= set(FIGURES), "RECORD_FIGURE_NOT_ALLOWED")
    _need(all(_figure(FIGURES[name], value) for name, value in figures.items()), "RECORD_FIGURES_INVALID")
    return payload


def import_record(payload, *, uploaded_by: str) -> dict:
    """Check one summary against the frozen inventory and store it. Repeating an upload adds nothing."""
    config.require_enabled()
    record = validate(payload)
    # The export time is not part of the content: exporting the same source twice is the same record.
    digest = tools.sha256({k: v for k, v in record.items() if k != "generated_at"})
    with tasks.connect() as conn:
        row = conn.execute(
            """SELECT admissible, execution_kind, record->'hashes' FROM office_strategy_versions
               WHERE strategy_id = %s AND version = %s""", (record["strategy_id"], record["version"])).fetchone()
        _need(row is not None, "STRATEGY_NOT_IN_INVENTORY")
        _need(row[0] and row[1] == "EXTERNAL_LOCAL", "STRATEGY_NOT_MIRRORED")
        _need(row[2] == record["rule_hashes"], "RULE_HASH_MISMATCH")
        done = conn.execute(
            """INSERT INTO office_external_records(record_id, strategy_id, version, record_name, first_session,
                   through_session, sessions, trades, trade_dates, figures, rule_hashes, source_file,
                   source_sha256, payload_sha256, generated_at, uploaded_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (strategy_id, version, through_session, source_sha256) DO NOTHING
               RETURNING record_id""",
            (uuid4(), record["strategy_id"], record["version"], record["record_name"], record["first_session"],
             record["through_session"], record["sessions"], record["trades"], record["trade_dates"],
             Jsonb(record["figures"]), Jsonb(record["rule_hashes"]), record["source"]["file"],
             record["source"]["sha256"], digest, record["generated_at"], uploaded_by)).fetchone()
        if done is None:
            stored = conn.execute(
                """SELECT payload_sha256 FROM office_external_records WHERE strategy_id = %s AND version = %s
                   AND through_session = %s AND source_sha256 = %s""",
                (record["strategy_id"], record["version"], record["through_session"],
                 record["source"]["sha256"])).fetchone()[0]
            _need(stored == digest, "RECORD_ALREADY_STORED_WITH_DIFFERENT_CONTENT")
    return {"status": "CREATED" if done is not None else "PRESENT", "strategy_id": record["strategy_id"],
            "version": record["version"], "through_session": record["through_session"],
            "simulated": True, "broker_fill": False}


def latest(conn) -> list[dict]:
    """The newest summary of each mirrored strategy version, with how many uploads it has had."""
    rows = conn.execute(
        """SELECT DISTINCT ON (strategy_id, version) strategy_id, version, record_name, first_session,
                  through_session, sessions, trades, trade_dates, figures, received_at,
                  count(*) OVER (PARTITION BY strategy_id, version)
           FROM office_external_records
           ORDER BY strategy_id, version, through_session DESC, received_at DESC""").fetchall()
    return [{"strategy_id": r[0], "version": r[1], "record_name": r[2], "first_session": str(r[3]),
             "through_session": str(r[4]), "sessions": r[5], "trades": r[6], "trade_dates": r[7],
             "figures": r[8], "received_at": r[9].isoformat(), "uploads": r[10],
             "kept": "ON_OWNER_MACHINE", "simulated": True, "broker_fill": False} for r in rows]
