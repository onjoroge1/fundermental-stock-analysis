# Coordinated production release of Agent Lab 0019

The release-only workflow uses the existing GitHub Production environment and
is serialized with `daily-data-forecast-refresh`. It does not run on PRs or
schedules. Initial push triggers are scoped to the release runner/workflow.
Manual reruns reuse `agent-pilot-initial-001` for each of the five stocks.

## Sequence

1. Require main and PR54 ancestry; allow only the explicitly reviewed release
   files and Alembic external-connection support since that application commit.
2. Wait for CI and the Vercel Git-build check on the exact main commit.
3. Check active/queued workflows and refuse to migrate under older workers.
4. Accept database revision 0018 or already-applied 0019 only. One SQLAlchemy
   transaction holds a PostgreSQL transaction-level advisory lock, sets LOCAL
   lock/statement timeouts, applies the exact Alembic 0019 migration, and verifies
   five tables and ten append-only triggers before commit. The Alembic env accepts
   that supplied connection; ordinary CLI/offline behavior is unchanged.
5. Verify production menu pages and the actual production journal API read.
6. When capture is disabled, use an existing `VERCEL_TOKEN` secret to set only
   `AGENT_LAB_ENABLED=true`, target production, on the fixed project/team. Create
   a new git-based production deployment pinned to the same SHA and verify the
   flag through the production API. No secrets are read back or exported.
7. With the existing `STOCK_MACHINE_ADMIN_TOKEN`, capture AAPL, MSFT, UBER, HIMS
   and VZ sequentially through the deployed API. Verify each detail read,
   original evidence hash, repeated-request identity, and DB event/outbox row.
8. Persist only credential-free status/IDs/hashes in the Actions report.

The original release attempt returned OperationalError before confirming the
migration. The runner no longer supplies PostgreSQL startup PGOPTIONS, which
can be incompatible with pooling proxies. Timeouts and locking now belong to
the actual migration transaction, not a separate pooled session. Neither the
configured database endpoint nor its credentials are rewritten. Diagnostics
return fixed categories, not raw DSNs, hosts, passwords or provider errors.

## Partial states and recovery

`DATABASE_URL` is required for migration. Capture uses the API and Vercel-owned
research inputs. No IBKR credential is passed to this workflow. No flag is
enabled by changing the application default or bypassing authentication.

The first run established that the GitHub Production context had DATABASE_URL
but no STOCK_MACHINE_ADMIN_TOKEN or VERCEL_TOKEN. Those absence checks disclose
no values. A missing Vercel credential stops enablement; migration verification
is reported separately. Alternatively set the flag in Vercel Production,
redeploy there, and rerun with the matching existing admin token configured in
GitHub Production. That manual flag route does not need a VERCEL_TOKEN in GitHub.

On a redeployment or capture timeout, inspect the report and actual deployment/
journal state before rerunning. Initial captures always use the same fixed key.
BLOCKED/NO_TRADE is an honest captured decision. FAILED means research could not
be obtained and the release is not fully verified. Never seed invented values.

This package changes no trading permissions, strategies, research rules, model
promotion or scheduled delivery. A successful migration is not activation.
