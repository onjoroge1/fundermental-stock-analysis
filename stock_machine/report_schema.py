"""Validation for persisted analyst reports.

The JSON schema is the contract between report producers, storage, the API,
and downstream decision tooling. Validation happens before any file or
database write so malformed reports cannot enter the evidence trail.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator

from .config import PROJECT_ROOT

SCHEMA_PATH = PROJECT_ROOT / "stock_machine" / "schemas" / "analysis_output.schema.json"


class AnalysisReportValidationError(ValueError):
    """Raised when an analysis report violates the persistence contract."""


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_analysis_report(
    report: dict[str, Any],
    *,
    expected_ticker: str | None = None,
    expected_as_of: str | None = None,
) -> None:
    """Validate schema plus request/report identity consistency."""
    errors = sorted(_validator().iter_errors(report), key=lambda e: list(e.path))
    if errors:
        details = []
        for error in errors[:10]:
            location = ".".join(str(part) for part in error.absolute_path)
            details.append(f"{location or '<root>'}: {error.message}")
        if len(errors) > 10:
            details.append(f"... and {len(errors) - 10} more error(s)")
        raise AnalysisReportValidationError(
            "analysis report failed schema validation: " + "; ".join(details)
        )

    if expected_ticker and report["ticker"].upper() != expected_ticker.upper():
        raise AnalysisReportValidationError(
            f"report ticker {report['ticker']!r} does not match request "
            f"ticker {expected_ticker.upper()!r}"
        )
    if expected_as_of and report["as_of"][:10] != expected_as_of[:10]:
        raise AnalysisReportValidationError(
            f"report as_of {report['as_of']!r} does not match request "
            f"as_of {expected_as_of!r}"
        )
