# Options provider error hotfix

Production research endpoints are independent from live IBKR connectivity.
This hotfix ensures live market-data initialization failures are represented as
HTTP 503 service-unavailable responses instead of generic HTTP 500 errors.

Covered cases include missing optional provider packages, unreachable TWS / IB
Gateway sockets, and unavailable Client Portal infrastructure. Invalid provider
names remain configuration errors.
