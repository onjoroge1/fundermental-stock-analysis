import copy

import pytest

from stock_machine.report_schema import (
    AnalysisReportValidationError,
    validate_analysis_report,
)


@pytest.fixture
def valid_report() -> dict:
    return {
        "analysis_schema_version": "1.0.0",
        "ticker": "SPY",
        "as_of": "2026-08-17T12:00:00Z",
        "data_sufficiency": {"status": "PASS"},
        "fundamental_trend": {
            "direction": "STABLE",
            "strength": "MODERATE",
        },
        "scenarios": [
            {"name": "base", "probability": 1.0, "fair_value": 650.0}
        ],
        "investment_thesis": {
            "summary": "Test fixture",
            "risks": [],
            "invalidation_conditions": [],
        },
        "adversarial_review": {
            "strongest_bear_case": "Test fixture",
            "fragile_assumptions": [],
        },
        "conclusion": {"classification": "WATCH", "conviction": "LOW"},
        "claims": [],
    }


def test_valid_report_passes(valid_report):
    validate_analysis_report(
        valid_report, expected_ticker="spy", expected_as_of="2026-08-17"
    )


def test_missing_required_field_is_rejected(valid_report):
    report = copy.deepcopy(valid_report)
    del report["claims"]
    with pytest.raises(AnalysisReportValidationError, match="claims"):
        validate_analysis_report(report)


def test_invalid_enum_is_rejected(valid_report):
    report = copy.deepcopy(valid_report)
    report["conclusion"]["classification"] = "STRONG_BUY"
    with pytest.raises(AnalysisReportValidationError, match="STRONG_BUY"):
        validate_analysis_report(report)


def test_request_identity_must_match_report(valid_report):
    with pytest.raises(AnalysisReportValidationError, match="does not match"):
        validate_analysis_report(valid_report, expected_ticker="QQQ")
