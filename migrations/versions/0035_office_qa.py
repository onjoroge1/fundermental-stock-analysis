"""Trading Office QA: scorecards, learning proposals and the record of every deployment state change.

Additive only. It creates three new ``office_`` tables and alters no existing table. It adds one trigger to
``office_deployments`` so that a state can only change together with a written reason. All three tables
are written once. Nothing here can make a deployment qualified or live.
"""
from alembic import op

revision = "0035_office_qa"
down_revision = "0034_office_external"
branch_labels = None
depends_on = None

SHA = "~ '^[0-9a-f]{64}$'"
ID = "~ '^[A-Za-z0-9._:-]{1,80}$'"


def upgrade():
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    # Two separate assessments per deployment and date: how the paper book was run, and what the strategy did.
    op.execute(f"""CREATE TABLE office_evaluations (
        evaluation_id UUID PRIMARY KEY,
        deployment_id UUID NOT NULL REFERENCES office_deployments(deployment_id),
        kind TEXT NOT NULL CHECK(kind IN ('OPERATIONAL','STRATEGY')),
        as_of DATE NOT NULL,
        maturity TEXT NOT NULL CHECK(maturity IN ('PENDING','MATURED','NOT_APPLICABLE')),
        verdict TEXT NOT NULL CHECK(verdict IN ('CONTINUE_COLLECTING','PAUSE_FOR_DATA_OR_EXECUTION_DEFECT',
            'RETIRE_UNDER_REGISTERED_RULE','REQUEST_QUALIFICATION_REVIEW')),
        scorecard JSONB NOT NULL,
        limitations JSONB NOT NULL DEFAULT '[]'::jsonb,
        protocol JSONB NOT NULL,
        policy_version TEXT NOT NULL,
        input_sha256 TEXT NOT NULL CHECK(input_sha256 {SHA}),
        simulated BOOLEAN NOT NULL DEFAULT true CHECK(simulated),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(deployment_id, kind, as_of),
        CONSTRAINT office_evaluations_operational_has_no_maturity
            CHECK((kind = 'OPERATIONAL') = (maturity = 'NOT_APPLICABLE')),
        CONSTRAINT office_evaluations_operational_verdicts CHECK(kind <> 'OPERATIONAL' OR
            verdict IN ('CONTINUE_COLLECTING','PAUSE_FOR_DATA_OR_EXECUTION_DEFECT')),
        CONSTRAINT office_evaluations_strategy_verdicts CHECK(kind <> 'STRATEGY' OR
            verdict <> 'PAUSE_FOR_DATA_OR_EXECUTION_DEFECT'),
        CONSTRAINT office_evaluations_pending_cannot_conclude CHECK(maturity = 'MATURED' OR
            verdict IN ('CONTINUE_COLLECTING','PAUSE_FOR_DATA_OR_EXECUTION_DEFECT')),
        CONSTRAINT office_evaluations_matured_concludes CHECK(kind <> 'STRATEGY' OR maturity <> 'MATURED' OR
            verdict IN ('RETIRE_UNDER_REGISTERED_RULE','REQUEST_QUALIFICATION_REVIEW')))""")
    # The registered evaluation is run once. A second look at a matured result is refused.
    op.execute("CREATE UNIQUE INDEX office_evaluations_one_matured ON office_evaluations(deployment_id) "
               "WHERE kind = 'STRATEGY' AND maturity = 'MATURED'")
    op.execute(f"""CREATE TABLE office_proposals (
        proposal_id UUID PRIMARY KEY,
        strategy_id TEXT NOT NULL,
        version TEXT NOT NULL,
        proposed_version TEXT NOT NULL CHECK(proposed_version {ID}),
        evaluation_id UUID REFERENCES office_evaluations(evaluation_id),
        reason TEXT NOT NULL CHECK(length(reason) BETWEEN 10 AND 500),
        change JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        FOREIGN KEY(strategy_id, version) REFERENCES office_strategy_versions(strategy_id, version),
        UNIQUE(strategy_id, proposed_version),
        CONSTRAINT office_proposals_new_version CHECK(proposed_version <> version))""")
    op.execute(f"""CREATE TABLE office_deployment_events (
        event_id UUID PRIMARY KEY,
        deployment_id UUID NOT NULL REFERENCES office_deployments(deployment_id),
        from_state TEXT NOT NULL,
        to_state TEXT NOT NULL CHECK(to_state IN ('EXPERIMENTAL_PAPER','PAUSED','RETIRED')),
        actor TEXT NOT NULL CHECK(actor ~ '^(owner:[A-Za-z0-9._@-]{{1,80}}|policy)$'),
        policy_version TEXT NOT NULL,
        reason_code TEXT NOT NULL CHECK(reason_code ~ '^[A-Z0-9_]{{3,60}}$'),
        evaluation_id UUID REFERENCES office_evaluations(evaluation_id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT office_deployment_events_policy_needs_evaluation
            CHECK(actor <> 'policy' OR evaluation_id IS NOT NULL),
        CONSTRAINT office_deployment_events_resume_is_the_owners
            CHECK(to_state <> 'EXPERIMENTAL_PAPER' OR actor <> 'policy'))""")
    op.execute("CREATE INDEX office_deployment_events_by_deployment ON office_deployment_events"
               "(deployment_id, created_at)")
    op.execute("""CREATE FUNCTION reject_office_qa_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Office QA records are written once' USING ERRCODE='55000'; END; $$""")
    for table in ("office_evaluations", "office_proposals", "office_deployment_events"):
        op.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_office_qa_change()")
        op.execute(f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION reject_office_qa_change()")
    # A proposal names a version that does not exist yet. It never points at, or replaces, a recorded one.
    op.execute("""CREATE FUNCTION check_office_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM office_strategy_versions s
                       WHERE s.strategy_id = NEW.strategy_id AND s.version = NEW.proposed_version) THEN
                RAISE EXCEPTION 'An office proposal must name a new version' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_proposals_checked BEFORE INSERT ON office_proposals "
               "FOR EACH ROW EXECUTE FUNCTION check_office_proposal()")
    # A state change is written down first: pause, resume or retire, from the state the deployment is in.
    # A change made by policy must rest on an evaluation of that deployment with the matching verdict.
    op.execute("""CREATE FUNCTION check_office_deployment_event() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_state TEXT;
        BEGIN
            SELECT state INTO current_state FROM office_deployments WHERE deployment_id = NEW.deployment_id;
            IF current_state IS DISTINCT FROM NEW.from_state OR NOT (
                   (NEW.from_state = 'EXPERIMENTAL_PAPER' AND NEW.to_state IN ('PAUSED','RETIRED'))
                OR (NEW.from_state = 'PAUSED' AND NEW.to_state IN ('EXPERIMENTAL_PAPER','RETIRED'))) THEN
                RAISE EXCEPTION 'That office deployment state change is not allowed' USING ERRCODE='55000';
            END IF;
            IF NEW.evaluation_id IS NOT NULL AND NOT EXISTS (
                   SELECT 1 FROM office_evaluations e
                   WHERE e.evaluation_id = NEW.evaluation_id AND e.deployment_id = NEW.deployment_id
                     AND ((NEW.to_state = 'PAUSED' AND e.verdict = 'PAUSE_FOR_DATA_OR_EXECUTION_DEFECT')
                       OR (NEW.to_state = 'RETIRED' AND e.verdict = 'RETIRE_UNDER_REGISTERED_RULE'))) THEN
                RAISE EXCEPTION 'The office evaluation does not support that state change' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_deployment_events_checked BEFORE INSERT ON office_deployment_events "
               "FOR EACH ROW EXECUTE FUNCTION check_office_deployment_event()")
    op.execute("""CREATE FUNCTION check_office_deployment_state_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.state IS DISTINCT FROM OLD.state AND NOT EXISTS (
                   SELECT 1 FROM office_deployment_events v
                   WHERE v.deployment_id = NEW.deployment_id AND v.from_state = OLD.state
                     AND v.to_state = NEW.state AND v.created_at = now()) THEN
                RAISE EXCEPTION 'An office deployment state change needs its written reason' USING ERRCODE='55000';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER office_deployments_state_change_recorded BEFORE UPDATE ON office_deployments "
               "FOR EACH ROW EXECUTE FUNCTION check_office_deployment_state_change()")


def downgrade():
    raise RuntimeError("Preserve the office QA records; use a reviewed forward migration")
