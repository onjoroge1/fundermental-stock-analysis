# I06: Owner observability and decision replay

/performance uses the existing /admin session and CSRF/origin policy. Its JSON data, report creation/cancellation and HTML attachments remain owner-authenticated. It exposes no live mode, arbitrary job, SQL, provider key or order API.

Reports are explicit jobs, not a side effect of viewing a chart. Generate report freezes a repeatable-read ledger/benchmark input, queues it on the existing control plane, and returns PENDING. The page honestly says an analytics worker must be available. Opening /performance itself never starts research or a trade.

The initial portfolio is legacy-agent-paper-v1. It is a truthful projection of existing saved simulated fills and marks, not a retrospective certification of executable trades. A future LEAN adapter will publish to the same event contract under a different designated portfolio/engine identity. Current equity, costs and history are withheld when required marks are missing. The page distinguishes occurrence, record and research price-basis dates.

Lightweight Charts 5.0.9 is downloaded ONLY at build time by scripts/build_chart_assets.py from its exact npm release. The archive's sha512 integrity is verified against registry metadata; files are individually selected, never extractall or install scripts. The generated JS is self-hosted under /ui/vendor with a SHA256 manifest and the upstream LICENSE. This is version pinning + recorded integrity, not an independently audited release checksum. Freeze the generated manifest in the approved deployment artifact.

Visible TradingView attribution and link are retained. Third-party source: https://github.com/tradingview/lightweight-charts/tree/v5.0.9, Apache-2.0 plus NOTICE attribution. No runtime CDN or additional credential is required. Missing JS leaves accessible table/evidence fallback rather than fake charts.

Tests: permission/CSRF/side-effect-free GET contracts; asset identity/integrity fixtures; dedicated CI installs the actual chart archive and Chromium, verifies real canvas rendering and event inspection on desktop/mobile with fixture APIs. Fixture data is never imported into production. Existing #79 remains open; the performance view reuses its read-only intelligence-projection idea without independently merging its dashboard modifications. Read-only private MCP portfolio exposure is deferred until it has an authenticated scope; the existing public research MCP is not expanded with private portfolio data.
