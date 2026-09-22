# Trusted analytics worker (I04)

Uses existing Postgres orchestration_jobs and server-side DATABASE_URL. No human admin token is needed for routine report jobs. This PR does not provision a worker host or enable a broker capability.

Web cron claims existing lightweight jobs. performance_report is worker-only. Run python -m stock_machine.integrations.workers --once for one job, or omit --once for a persistent service on the chosen host.

Each trusted analytics child has a 300s wall-clock limit, 240s CPU, 4 GB virtual address space and 16 MB file-output limit (Linux). Heartbeats renew the attempt; cancellation/expiry fence publication. Work is at-least-once; existing research/provider side effects retain their own idempotency contracts. Cancellation does not undo committed work.

Bounded JSON outputs/checkpoints use the existing append-only evidence store. Same-key/different-payload submissions fail. This is NOT an untrusted-code/RD-Agent sandbox. Never execute generated code here or mount a host container socket.

Release gate: pin PYTHON_BASE to an approved sha256 digest, freeze transitive worker dependencies in the deployment image, run PostgreSQL fencing tests and a real child-process smoke test, then record image digest with deployment metadata. The development Dockerfile alone is not a frozen production environment. Do not add research/broker secrets to this image.
