# Initial agent capture on the reviewed post-PR62 runtime

The user confirmed that AGENT_LAB_ENABLED was set in Vercel Production and a
new deployment created, and that STOCK_MACHINE_ADMIN_TOKEN was added to GitHub.
A live read confirmed capture_enabled=true and no journal entries. The deployed
commit was c3e93bb6be70388fe8604cfaa2d4b9955382f04e (merged PR62).

## Why this is a capture-only continuation

Main has progressed beyond PR57: source-integrity controls, the Massive adapter,
bounded source briefs, maintenance and price-freshness fixes are already merged.
The declared database version is now 0021_monitoring_storage. The historical 0019
runner must not be used to downgrade, migrate or bypass its old runtime guard.

This workflow makes no application changes and performs no migration. A reviewed
base SHA plus a four-file allowlist rejects further runtime drift. Current-main
CI/build and old-active-worker checks are reused from the newer integrity release.
Database revision and the ten original journal protection triggers are verified
using a read-only transaction. The production flag must already be enabled.

## Bounded operation

On main only, using the existing Production environment and existing admin token:
1. Refresh AAPL, MSFT, UBER, HIMS and VZ source briefs through the established API.
   Each fresh cycle permits at most two Massive requests; keys stay on Vercel.
2. Repeat each cycle request with the same key and verify identity, not new work.
3. Capture the five journal decisions through the existing guarded API. Preserve
   agent-pilot-initial-001 and agent-pilot-activation-v1 on every workflow retry.
4. Read each stored decision, frozen evidence, contract/report references, then
   repeat capture and verify the original decision returns. Database reads prove
   exactly one initial event and outbox row for each of the five request IDs.
5. Exercise real production MCP initialize, initialized notification, eight-tool
   discovery, get_agent_status and list_agent_decisions. MCP gets no admin token.

Only summaries, source dates, decision IDs, hashes and safe status codes are
persisted in the Actions report. No credential values, provider raw responses,
news text or database URL is printed. Missing credentials/authentication stop
without attempting alternate or weaker authorization.

BLOCKED/NO_TRADE can be a correctly captured research outcome. Financial source
checks must not be weakened to turn it into WATCH. Provider access is reported
separately: CAPTURES_VERIFIED_PROVIDER_INCOMPLETE does not claim Massive works.
A failure after some writes preserves those completed steps in the report;
retries use the same keys rather than overwrite old evidence.

No trade, simulated fill, reward, learned policy, new recurring schedule, or
ChatGPT connection installation is authorized or implemented by this workflow.
The existing maintenance schedule is unchanged. Code publication, production
API writes and an actual successful MCP handshake are separate checkpoints.
