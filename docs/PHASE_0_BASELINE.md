# Phase 0 baseline

Baseline captured on 2026-08-17 from `main` commit
`a49887bbce24b62b3066d823e3e321bb3acdc11a`.

## Product boundary

The current system is an evidence-first equity research application. It
ingests point-in-time fundamental and price data, computes deterministic
metrics, produces evidence-cited analysis reports, generates probabilistic
price forecasts, and tracks paper signals. IBKR support is currently limited
to read-only Flex statements; options market data, strategy generation, and
order routing are intentionally outside this baseline.

## Baseline findings

| Area | Baseline | Phase 0/1 response |
|---|---|---|
| Change control | Session-end hook staged all files and pushed to `main` | Hook and auto-push script removed; work uses branches and PR review |
| CI | No workflow | Python 3.11/3.12 test workflow added |
| Installation | Runtime imports missing from `pyproject.toml` | Runtime, development, and optional prediction dependencies declared |
| Report contract | JSON schema existed but save paths did not enforce it | Packaged schema validation now occurs before persistence |
| Database evolution | Schema created from application startup SQL | Alembic baseline introduced; legacy bootstrap retained temporarily |
| Configuration | IBKR and data-path settings undocumented | `.env.example` now describes every supported variable |
| Repository hygiene | `.DS_Store` committed and not ignored | File removed and ignore rules expanded |
| Licensing | Public repository has no license | Owner decision required before adding one |

## Quality gate

Phase 1 is complete when a clean environment can install the declared package,
all unit tests pass on Python 3.11 and 3.12, invalid reports are rejected before
any write, and the baseline database migration can upgrade an empty Postgres
database. Live provider and database integration tests remain a later phase
because they require credentials and isolated infrastructure.
