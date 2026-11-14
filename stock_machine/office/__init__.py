"""Trading Office: research, PAPER trading and review work run as queued tasks.

Paper and simulation only. Nothing in this package may import a broker client, the IBKR bridge or the
execution desk, or place an order; ``tests/test_office_boundary.py`` enforces that.
"""
