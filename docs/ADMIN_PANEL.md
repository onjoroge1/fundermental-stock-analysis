# Owner admin panel — simplified operations

`/admin` is the human control plane for the research system. The operating
model is intentionally small: one owner login, one database-backed research
pause switch, one five-stock research action, and read-only operational status.

## Human login

The owner username is fixed as `admin`. The browser never asks for an API token.
There is no public registration or token-based bootstrap form.

One Vercel Production secret, `ADMIN_PASSWORD`, supplies the initial password.
On the first successful `admin` login after migration 0022, the application
stores a salted Argon2id hash in PostgreSQL and creates a normal secure session.
Later logins use the database hash. The raw password is never written to the
repository, database, browser storage, logs, reports or audit records.

This replaces the previous human-facing `AGENT_LAB_ENABLED` setup burden, so the
number of operational environment variables does not increase: remove
`AGENT_LAB_ENABLED`, add `ADMIN_PASSWORD`. Machine credentials remain invisible
to the admin UI.

Sessions use a host-only Secure/HttpOnly/SameSite=Strict cookie, eight-hour
absolute expiry, thirty-minute idle expiry, CSRF protection for writes, and a
persistent login throttle. Passwords may be changed from the panel; doing so
revokes the previous session.

## Research control

`operator_controls.capture_paused` is the single routine research switch:

- `false` → research capture is running.
- `true` → new journal captures and manual pilot steps are paused.

Changing this value does not require a Vercel redeploy. The existing machine
bearer APIs still require `STOCK_MACHINE_ADMIN_TOKEN`, but that credential is
for server-to-server authorization only and is never entered by a person in the
admin panel.

The admin switch does not enable trading. Broker execution, simulated fills,
rewards, reinforcement learning and policy promotion remain unavailable from
these routes.

## Five-stock pilot

The owner can start a bounded AAPL/MSFT/UBER/HIMS/VZ research review. Progress
is durable and resumable. Each stock reuses the existing bounded research-cycle
and immutable journal services. Financial/source/freshness checks remain
controlling, and a successful operational run may still produce BLOCKED or
NO_TRADE research outcomes.

## Secrets that remain server-side

Only infrastructure or machine credentials belong in Vercel:

- `DATABASE_URL`
- `ADMIN_PASSWORD` (initial human login only)
- `STOCK_MACHINE_ADMIN_TOKEN` (machine-to-machine APIs)
- `CRON_SECRET`
- `MASSIVE_API` / provider credentials
- broker credentials when configured

None of these values are displayed by `/admin`.

## Release dependency

Migration `0022_admin_panel` must be applied before the admin login and controls
can operate. The simplification PR does not add another migration. It depends on
the migration hotfix being completed and the repository schema sentinel being
aligned to `0022_admin_panel` in the controlled release sequence.

Normal operation after release should require no GitHub Actions interaction:
visit `/admin`, sign in, pause/resume research, run the five-stock review, and
inspect results.
