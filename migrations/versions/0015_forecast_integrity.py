"""Immutable forecast identity and independent retrieval timestamps."""
from alembic import op

revision = "0015_forecast_integrity"
down_revision = "0014_api_control_plane"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE dataset_snapshots ADD COLUMN last_checked_at TIMESTAMPTZ")
    op.execute("UPDATE dataset_snapshots SET last_checked_at = observed_at")
    op.execute("ALTER TABLE dataset_snapshots ALTER COLUMN last_checked_at SET DEFAULT now()")
    op.execute("ALTER TABLE dataset_snapshots ALTER COLUMN last_checked_at SET NOT NULL")
    op.execute("ALTER TABLE prediction_forecasts ADD COLUMN forecast_id TEXT")
    op.execute("""UPDATE prediction_forecasts SET forecast_id = 'legacy_' ||
                  encode(sha256(convert_to(ticker || as_of::text || model_version || payload::text, 'UTF8')), 'hex')""")
    op.execute("ALTER TABLE prediction_forecasts ALTER COLUMN forecast_id SET NOT NULL")
    # Link existing outcomes BEFORE allowing multiple artifacts per old key.
    op.execute("ALTER TABLE alpha_probability_outcomes ADD COLUMN forecast_id TEXT")
    op.execute("""UPDATE alpha_probability_outcomes o SET forecast_id = f.forecast_id
                  FROM prediction_forecasts f WHERE o.ticker=f.ticker AND o.as_of=f.as_of
                  AND o.model_version=f.model_version""")
    op.execute("""UPDATE alpha_probability_outcomes SET forecast_id = 'orphan_' ||
                  encode(sha256(convert_to(ticker || as_of::text || model_version, 'UTF8')), 'hex')
                  WHERE forecast_id IS NULL""")
    op.execute("ALTER TABLE alpha_probability_outcomes ALTER COLUMN forecast_id SET NOT NULL")
    op.drop_constraint("alpha_probability_outcomes_pkey", "alpha_probability_outcomes", type_="primary")
    op.create_primary_key("alpha_probability_outcomes_pkey", "alpha_probability_outcomes", ["forecast_id", "horizon_days"])
    op.drop_constraint("prediction_forecasts_pkey", "prediction_forecasts", type_="primary")
    op.create_primary_key("prediction_forecasts_pkey", "prediction_forecasts", ["forecast_id"])
    op.create_index("prediction_forecasts_latest_idx", "prediction_forecasts", ["ticker", "as_of", "generated_at"])


def downgrade():
    raise RuntimeError("Forecast identities are append-only; restore a reviewed backup rather than collapsing forecast vintages")
