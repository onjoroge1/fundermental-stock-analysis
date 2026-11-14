"""The strategy inventory: one record per exact strategy version, copied from its source record.

The checked-in file ``strategy_inventory.json`` is the only source. It holds versions, hashes, figures,
defects and each strategy's original status. It holds no market data. Loading it copies each record into
``office_strategy_versions`` once. A record already stored under the same version is never changed: a
different record needs a new version, so a closed or failed strategy keeps the label its own record gave it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from psycopg.types.json import Jsonb

from . import config, tasks, tools

PATH = Path(__file__).with_name("strategy_inventory.json")
COLUMNS = ("strategy_id", "version", "source_repository", "evidence_tier", "original_status", "admissible",
           "execution_kind", "risk_group")
RECORD_KEYS = ("rule_summary", "hashes", "development", "shorts", "defects", "forward_protocol",
               "runner_status", "display_notes")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def validate(row: dict) -> dict:
    """Check one inventory record's shape. An excluded strategy must say why and can have no execution."""
    missing = [k for k in (*COLUMNS, *RECORD_KEYS) if k not in row]
    if missing:
        raise ValueError(f"INVENTORY_RECORD_INCOMPLETE:{row.get('strategy_id')}:{','.join(missing)}")
    if not isinstance(row["admissible"], bool) or not isinstance(row["hashes"], dict):
        raise ValueError(f"INVENTORY_RECORD_INVALID:{row['strategy_id']}")
    if any(not isinstance(h, str) or not _SHA.match(h) for h in row["hashes"].values()):
        raise ValueError(f"INVENTORY_HASH_INVALID:{row['strategy_id']}")
    if row["admissible"] == (row["execution_kind"] == "NONE"):
        raise ValueError(f"INVENTORY_EXECUTION_KIND_INVALID:{row['strategy_id']}")
    if not row["admissible"] and not row.get("exclusion_reason"):
        raise ValueError(f"INVENTORY_EXCLUSION_REASON_MISSING:{row['strategy_id']}")
    return row


def read(path: Path = PATH) -> list[dict]:
    rows = [validate(r) for r in json.loads(path.read_text())["strategies"]]
    keys = [(r["strategy_id"], r["version"]) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("INVENTORY_VERSION_REPEATED")
    return rows


def load(rows: list[dict] | None = None) -> dict:
    """Copy the inventory into the database. Repeating it adds nothing; a changed record is refused."""
    config.require_enabled()
    rows = read() if rows is None else [validate(r) for r in rows]
    created = 0
    with tasks.connect() as conn:
        for row in rows:
            record = {k: v for k, v in row.items() if k not in COLUMNS}
            digest = tools.sha256({k: row[k] for k in (*COLUMNS, *sorted(record))})
            done = conn.execute(
                """INSERT INTO office_strategy_versions(strategy_id, version, source_repository, evidence_tier,
                       original_status, admissible, execution_kind, risk_group, record, record_sha256)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (strategy_id, version) DO NOTHING RETURNING strategy_id""",
                (*(row[c] for c in COLUMNS), Jsonb(record), digest)).fetchone()
            if done is None:
                stored = conn.execute(
                    "SELECT record_sha256 FROM office_strategy_versions WHERE strategy_id = %s AND version = %s",
                    (row["strategy_id"], row["version"])).fetchone()[0]
                if stored != digest:
                    raise ValueError("INVENTORY_VERSION_ALREADY_RECORDED_WITH_DIFFERENT_CONTENT:"
                                     f"{row['strategy_id']}:{row['version']}")
            created += done is not None
    return {"records": len(rows), "created": created}
