"""The office spend ledger. Every paid model call reserves its maximum cost before it is sent.

Rules, each covered by a test against real Postgres:

* The daily and monthly caps are checked inside the transaction that inserts the reservation, under an
  advisory lock, so two workers cannot both slip under a cap.
* What counts against a cap is the actual cost once known, and the reserved maximum until then. A call whose
  cost never becomes known is charged at its maximum, never at zero.
* A reservation is released (charged nothing) only when the call provably did not happen.
* Asking to reserve a call number that was already reserved and never settled means an earlier attempt may
  have reached the provider. That reservation becomes UNRESOLVED and the call is refused, not sent again.

Days and months are counted in UTC. Amounts are US dollars as ``Decimal``.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from psycopg.types.json import Jsonb

from .. import db
from . import config

BUDGET_LOCK = 0x0FF1CE02
_CHARGED = "CASE WHEN state = 'RELEASED' THEN 0 ELSE COALESCE(actual_usd, reserved_usd) END"


class BudgetExceeded(RuntimeError):
    """The reservation would pass a cap. ``code`` is DAILY_BUDGET_EXCEEDED or MONTHLY_BUDGET_EXCEEDED."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UncertainCall(RuntimeError):
    """A paid call may already have been sent and its result was never recorded. Do not send it again."""

    def __init__(self, code: str = "UNCERTAIN_EXTERNAL_CALL"):
        super().__init__(code)
        self.code = code


def connect():
    return db.connect()


def _spent(cur) -> tuple[Decimal, Decimal]:
    cur.execute(
        f"""SELECT
              COALESCE(SUM({_CHARGED}) FILTER (WHERE created_at >=
                  date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'), 0),
              COALESCE(SUM({_CHARGED}) FILTER (WHERE created_at >=
                  date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'), 0)
            FROM office_budget_reservations
            WHERE created_at >= date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'""")
    day, month = cur.fetchone()
    return Decimal(day), Decimal(month)


def spent() -> dict:
    """Dollars counted against today's and this month's caps, with the caps in force."""
    with connect() as conn, conn.cursor() as cur:
        day, month = _spent(cur)
    return {"day_usd": day, "month_usd": month, "daily_cap_usd": config.daily_usd_cap(),
            "monthly_cap_usd": config.monthly_usd_cap()}


def reserve(run_id: str, call_number: int, provider: str, model: str, max_usd: Decimal) -> str:
    """Reserve the maximum cost of one call and return the reservation id, or raise without reserving."""
    config.require_enabled()
    max_usd = Decimal(max_usd)
    if not max_usd.is_finite() or max_usd <= 0:
        raise ValueError("max_usd must be a positive amount")
    if max_usd > config.MAX_SINGLE_CALL_USD:
        raise BudgetExceeded("SINGLE_CALL_BUDGET_EXCEEDED")
    reservation_id, uncertain = str(uuid4()), False
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (BUDGET_LOCK,))
        cur.execute("SELECT reservation_id, state FROM office_budget_reservations "
                    "WHERE run_id = %s AND call_number = %s FOR UPDATE", (run_id, call_number))
        existing = cur.fetchone()
        if existing is not None:
            if existing[1] not in ("RESERVED", "UNRESOLVED"):
                raise ValueError("CALL_NUMBER_ALREADY_SETTLED")
            cur.execute("UPDATE office_budget_reservations SET state = 'UNRESOLVED', settled_at = now() "
                        "WHERE reservation_id = %s AND state = 'RESERVED'", (existing[0],))
            uncertain = True          # commit the UNRESOLVED mark, then refuse below
        else:
            day, month = _spent(cur)
            if day + max_usd > config.daily_usd_cap():
                raise BudgetExceeded("DAILY_BUDGET_EXCEEDED")
            if month + max_usd > config.monthly_usd_cap():
                raise BudgetExceeded("MONTHLY_BUDGET_EXCEEDED")
            cur.execute(
                """INSERT INTO office_budget_reservations(reservation_id, run_id, call_number, provider,
                       model, reserved_usd) VALUES (%s, %s, %s, %s, %s, %s)""",
                (reservation_id, run_id, call_number, provider, model, max_usd))
    if uncertain:
        raise UncertainCall()
    return reservation_id


def _settle(reservation_id: str, state: str, actual_usd, usage: dict | None) -> None:
    with connect() as conn:
        row = conn.execute(
            """UPDATE office_budget_reservations SET state = %s, actual_usd = %s, usage = %s,
                   settled_at = now() WHERE reservation_id = %s AND state = 'RESERVED'
               RETURNING reservation_id""",
            (state, actual_usd, Jsonb(usage or {}), reservation_id)).fetchone()
    if row is None:
        raise ValueError("RESERVATION_NOT_OPEN")


def reconcile(reservation_id: str, actual_usd: Decimal | None, usage: dict | None = None) -> None:
    """Record what a completed call cost. An unknown cost is charged at the reserved maximum."""
    if actual_usd is None:
        with connect() as conn:
            row = conn.execute("SELECT reserved_usd FROM office_budget_reservations WHERE reservation_id = %s",
                               (reservation_id,)).fetchone()
        if row is None:
            raise ValueError("RESERVATION_NOT_OPEN")
        actual_usd, usage = row[0], {**(usage or {}), "cost_unknown_charged_at_maximum": True}
    actual_usd = Decimal(actual_usd)
    if not actual_usd.is_finite() or actual_usd < 0:
        raise ValueError("actual_usd must be zero or more")
    _settle(reservation_id, "RECONCILED", actual_usd, usage)


def release(reservation_id: str) -> None:
    """Charge nothing. Only for a call that provably was not made or was rejected without being run."""
    _settle(reservation_id, "RELEASED", None, None)


def mark_unresolved(reservation_id: str) -> None:
    """The call may have run but its result is unknown. It stays charged at the maximum."""
    _settle(reservation_id, "UNRESOLVED", None, None)


def resolve_not_made(task_id: str, *, reference: str) -> dict:
    """The owner states that a blocked task's uncertain calls were never sent. Their reservations stop being
    charged and the task goes back to the queue with its attempts reset.

    Only for a task that is BLOCKED with UNCERTAIN_EXTERNAL_CALL. The reservation rows are kept: each records
    that it was released by this statement and the reference given (for example the GitHub run that did it).
    """
    note = Jsonb({"resolved": "CALL_NOT_MADE", "stated_by": "owner", "reference": str(reference)[:120]})
    with connect() as conn:
        task = conn.execute("SELECT state, error_code FROM office_tasks WHERE task_id = %s FOR UPDATE",
                            (task_id,)).fetchone()
        if task is None or tuple(task) != ("BLOCKED", "UNCERTAIN_EXTERNAL_CALL"):
            raise ValueError("TASK_NOT_BLOCKED_ON_AN_UNCERTAIN_CALL")
        released = conn.execute(
            """UPDATE office_budget_reservations b SET state = 'RELEASED', usage = COALESCE(b.usage, '{}'::jsonb) || %s
               FROM office_runs r WHERE r.run_id = b.run_id AND r.task_id = %s AND b.state = 'UNRESOLVED'
               RETURNING b.reservation_id""", (note, task_id)).fetchall()
        conn.execute(
            """UPDATE office_tasks SET state = 'QUEUED', error_code = NULL, due_at = now(), updated_at = now(),
                   attempts = 0 WHERE task_id = %s""", (task_id,))
    return {"task_id": str(task_id), "released": len(released), "state": "QUEUED"}
