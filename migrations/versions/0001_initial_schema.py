"""Baseline the existing Stock Machine schema.

Revision ID: 0001
Revises: None
Create Date: 2026-08-17
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


INITIAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS companies (
    ticker TEXT PRIMARY KEY,
    cik TEXT NOT NULL,
    legal_name TEXT,
    exchange TEXT,
    sic_description TEXT,
    fiscal_year_end TEXT,
    reporting_currency TEXT DEFAULT 'USD',
    updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS filings (
    ticker TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    form TEXT,
    filed_at DATE,
    report_date DATE,
    primary_document TEXT,
    PRIMARY KEY (ticker, accession_number)
);
CREATE TABLE IF NOT EXISTS financial_periods (
    ticker TEXT NOT NULL,
    duration_type TEXT NOT NULL,
    period_end DATE NOT NULL,
    period_start DATE,
    fiscal_year INT,
    fiscal_period TEXT,
    filed_at DATE,
    available_at DATE,
    form TEXT,
    accession_number TEXT,
    derived BOOLEAN DEFAULT FALSE,
    fields JSONB NOT NULL,
    field_sources JSONB NOT NULL,
    PRIMARY KEY (ticker, duration_type, period_end)
);
CREATE TABLE IF NOT EXISTS prices_daily (
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION,
    close DOUBLE PRECISION, adj_close DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    action_type TEXT NOT NULL,
    value DOUBLE PRECISION,
    PRIMARY KEY (ticker, date, action_type)
);
CREATE TABLE IF NOT EXISTS shares_outstanding (
    ticker TEXT NOT NULL,
    as_of DATE NOT NULL,
    shares DOUBLE PRECISION,
    available_at DATE,
    accession_number TEXT,
    PRIMARY KEY (ticker, as_of, shares)
);
CREATE TABLE IF NOT EXISTS consensus_snapshots (
    ticker TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    period_type TEXT NOT NULL,
    forecast_period_end DATE NOT NULL,
    revenue_mean DOUBLE PRECISION, revenue_high DOUBLE PRECISION,
    revenue_low DOUBLE PRECISION,
    eps_mean DOUBLE PRECISION, eps_high DOUBLE PRECISION,
    eps_low DOUBLE PRECISION,
    analyst_count INT,
    PRIMARY KEY (ticker, snapshot_date, period_type, forecast_period_end)
);
CREATE TABLE IF NOT EXISTS earnings_surprises (
    ticker TEXT NOT NULL,
    date DATE NOT NULL,
    actual_eps DOUBLE PRECISION,
    estimated_eps DOUBLE PRECISION,
    surprise_pct DOUBLE PRECISION,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS data_quality_events (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT,
    recorded_at TIMESTAMPTZ DEFAULT now(),
    event JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
    ticker TEXT NOT NULL,
    as_of DATE NOT NULL,
    metrics JSONB NOT NULL,
    PRIMARY KEY (ticker, as_of)
);
ALTER TABLE companies ADD COLUMN IF NOT EXISTS sic TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS sector TEXT;
CREATE TABLE IF NOT EXISTS insider_transactions (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    accession TEXT NOT NULL,
    filed_at DATE,
    transaction_date DATE,
    owner TEXT,
    role TEXT,
    code TEXT,
    acquired BOOLEAN,
    shares DOUBLE PRECISION,
    price DOUBLE PRECISION,
    value DOUBLE PRECISION,
    plan_10b5_1 BOOLEAN,
    classification TEXT,
    UNIQUE (accession, owner, transaction_date, code, shares, price)
);
CREATE TABLE IF NOT EXISTS forecast_outcomes (
    report_id TEXT NOT NULL,
    horizon TEXT NOT NULL,
    ticker TEXT NOT NULL,
    as_of DATE NOT NULL,
    target_date DATE NOT NULL,
    base_price DOUBLE PRECISION,
    actual_price DOUBLE PRECISION,
    actual_return_pct DOUBLE PRECISION,
    expected_return_pct DOUBLE PRECISION,
    error_pct DOUBLE PRECISION,
    in_range BOOLEAN,
    direction_hit BOOLEAN,
    classification TEXT,
    scored_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (report_id, horizon)
);
CREATE TABLE IF NOT EXISTS sm_backtest_runs (
    run_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT now(),
    params JSONB NOT NULL,
    summary JSONB
);
CREATE TABLE IF NOT EXISTS backtest_observations (
    run_id TEXT NOT NULL,
    as_of DATE NOT NULL,
    ticker TEXT NOT NULL,
    sector TEXT,
    composite DOUBLE PRECISION,
    components JSONB,
    factors JSONB,
    forward JSONB,
    PRIMARY KEY (run_id, as_of, ticker)
);
CREATE TABLE IF NOT EXISTS analysis_reports (
    report_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    as_of TIMESTAMPTZ,
    saved_at TIMESTAMPTZ DEFAULT now(),
    report JSONB NOT NULL
);
"""


def upgrade() -> None:
    for statement in INITIAL_SCHEMA_SQL.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in (
        "analysis_reports",
        "backtest_observations",
        "sm_backtest_runs",
        "forecast_outcomes",
        "insider_transactions",
        "metric_snapshots",
        "data_quality_events",
        "earnings_surprises",
        "consensus_snapshots",
        "shares_outstanding",
        "corporate_actions",
        "prices_daily",
        "financial_periods",
        "filings",
        "companies",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
