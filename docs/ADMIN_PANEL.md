# Owner admin panel v1

`/admin` provides username/password sign-in, password replacement, audited
capture pause/resume, three bounded manual five-stock runs per UTC day,
resumable per-stock progress, existing Massive observations and run history.
The main sidebar links to Admin. No new environment variable is required.

## Credentials and one-time setup

There is no default password, public-registration path, committed password
hash, or credential seeded by migration. The requested owner username is
`admin`. Never copy a password supplied in chat into source, fixtures, CI,
HTML, screenshots or issue comments.

After the controlled migration/deployment, open `/admin` on the canonical
production domain. The initial form requires the existing Vercel Production
`STOCK_MACHINE_ADMIN_TOKEN` as proof of ownership, and the chosen initial
password. That token is used only as an Authorization header for this one
setup request; it is not saved in browser storage or the owner database.
The existing bearer validator must accept it. Setup cannot circumvent an
incorrect token or a 401. The first owner insert and audit event are atomic;
repeat setup is rejected, including after the account is disabled.

Initial passwords must have 12-128 characters. First login requires replacing
that password with a different, private 15-128-character password before any
operational action is available. Do not reuse a password disclosed in chat.
Subsequent use requires only username/password. GitHub's copy of the API admin
token is not involved in panel login or manual pilot operation.

Passwords are salted Argon2id (19 MiB, two iterations, parallelism one). Owner
sessions are random opaque tokens with only SHA256 token digests stored in
PostgreSQL. SHA256 is NOT used for password hashing. The cookie is host-only,
Secure, HttpOnly and SameSite=Strict. Absolute expiry is eight hours, idle
expiry thirty minutes. One owner session is permitted; a new login or password
change revokes the old one. Logout deletes the session. Login attempts have a
persistent cross-worker limit of ten per five-minute window. Origin checks
and a per-session CSRF token protect mutations; preview mutations are blocked.
Responses use no-store, CSP, frame restrictions and fixed safe error messages.

## Controls versus secrets

`AGENT_LAB_ENABLED` remains the existing deployment-level safety restriction.
When true, `operator_controls.capture_paused` becomes the routine panel switch.
Changing the database switch does not require redeployment. It affects the
existing authenticated capture API as well as new panel pilot steps, so MCP's
read-only agent status reports the effective value. Missing control storage
fails closed; the panel cannot override an environment restriction.

This first control is deliberately scoped to journal captures and manual
pilot steps. It does NOT turn off the existing price/news maintenance jobs or
change their schedule. No scheduler editor or new recurring job is installed.
An in-flight request may finish after a pause; new steps check the switch again.

## Pilot execution

The owner starts one durable run, with five task rows in one transaction.
The browser sends one authenticated, CSRF-checked step request per stock,
sequentially while the page is open. Closing the tab does not erase progress,
but no claim is made that the remaining steps execute autonomously. Resume
continues pending or expired-lease tasks. A task has at most three claims and a
ten-minute lease; completion is fenced by its unique lease token. An expired
final attempt becomes a failure, not a forever-running task.

A step calls the existing bounded `research_cycle.run`, then the existing
`journal.capture`. Both use `panel:<run UUID>` as the stable ticker-scoped
idempotency key. Existing financial/source/freshness checks remain controlling.
A completed task can contain a BLOCKED/NO_TRADE decision: operational success
is not financial qualification. Provider unavailability is reported separately.
No trade, broker session, fill, position, simulated P&L, reward, or promotion
API is exposed by the owner routes. The legacy bearer/MCP interfaces are not
converted into cookie-authenticated arbitrary command gateways.

## Controlled release

Migration 0022 adds only owner/authentication/control/run tables and append-only
audit protection. It never creates an account. Apply it through the existing
controlled PostgreSQL release procedure, coordinated with matching app and
workers. Do not weaken the exact-version guard or stamp without migration.

**Publication blocker:** the attempted one-line update in `stock_machine/db.py`
was blocked by the repository tool with an indeterminate safety-status error.
It was NOT applied and no alternate write was used. Before this PR may merge,
`REQUIRED_SCHEMA_VERSION` must be reviewed and changed from
`0021_monitoring_storage` to `0022_admin_panel`, then full CI must pass.
The migration-head regression must remain enabled and is expected to detect
this pending mismatch. Do not deploy/apply 0022 while that declaration remains
0021. Old 0019/0020 release runners and PR63's frozen activation baseline are
historical checkpoints; do not use them to activate this new runtime.

Code publication, migration, deployment, account provisioning and a successful
five-stock run are separate checks. This PR does not establish any production
account or successful authenticated run. Provider and admin secret values
are never copied into repository files.

## Tests

`test_admin_panel.py` covers HTTP/session boundaries, secret redaction, origin,
CSRF, cookie flags, body limits, static routing, blocked execution capabilities,
and shared pause enforcement. `test_admin_panel_postgres.py` uses isolated
PostgreSQL schemas for bootstrap, hashing, session revocation/expiry, throttling,
optimistic settings concurrency, audit immutability, idempotency, budgets and
lease fencing. Synthetic passwords occur only in tests, never runtime seeds.

Further hardening: add MFA/passkeys and independently review the login/session
surface before broadening privileges or introducing multiple operators.
