# Coordinated production release of Agent Lab 0019

The release-only workflow uses the existing GitHub Production environment and
is serialized with `daily-data-forecast-refresh`. It does not run on PRs or
schedules. Its initial push trigger is scoped only to the release workflow and
runner. Manual reruns reuse `agent-pilot-initial-001` for each of the five stocks.

## Sequence

1. Require main and PR54 ancestry; refuse unreviewed runtime changes since PR54.
2. Wait for CI and the Vercel Git-build check on the exact main commit.
3. Check active/queued workflows and refuse to migrate under older workers.
4. Accept database revision 0018 or already-applied 0019 only. Apply the exact
   0019 migration, then verify the application version, five tables and ten
   enabled append-only triggers. A session advisory lock prevents two release
   runners from entering the migration section simultaneously.
5. Verify production menu pages and the actual production journal API read.
6. When capture is disabled, use an existing `VERCEL_TOKEN` secret to set only
   `AGENT_LAB_ENABLED=true`, target production, on the fixed project/team. Create
   a new git-based production deployment pinned to the same SHA and verify the
   flag through the production API. No secrets are read back or exported.
7. With the existing `STOCK_MACHINE_ADMIN_TOKEN`, capture AAPL, MSFT, UBER, HIMS
   and VZ sequentially through the deployed API. Verify each detail read,
   original evidence hash, repeated-request identity, and DB event/outbox row.
8. Persist only credential-free status/IDs/hashes in the Actions report.

`DATABASE_URL` remains necessary for the actual migration runner. Capture uses
the API and Vercel-owned research inputs. No IBKR credential is passed to this
workflow. No flag is enabled by changing its application default or disabling
an authentication check.

## Partial states and recovery

A missing Vercel credential stops enablement after migration verification; it
does not falsely report five captures or undo an additive audit migration.
Supply authorized deployment access or set the production flag through Vercel,
redeploy, and rerun the same workflow. A missing admin token stops API writes.
Authentication/permission failures are not retried by weakening controls.

On a redeployment or capture timeout, inspect the report and actual deployment/
journal state before rerunning. Initial captures always use the same fixed key,
so retries cannot reset their age or create duplicate research decisions.
BLOCKED/NO_TRADE is an honest captured decision. FAILED means research could not
be obtained and the release is not fully verified. Do not seed invented market
values to turn either state green.

This package changes no trading permissions, strategies, research rules, model
promotion, or scheduled-delivery behavior. The report records each completed
checkpoint separately; a migration success alone is not capture activation.
