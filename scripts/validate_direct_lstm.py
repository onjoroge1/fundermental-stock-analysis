"""Read-only, dated LSTM evaluation; never promotes or writes forecasts."""
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from stock_machine.db import connect, fetch_prices
from stock_machine.prediction import log_returns, validate, MODEL_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--as-of', required=True, type=date.fromisoformat)
    parser.add_argument('--output', default='direct-lstm-validation.json')
    args = parser.parse_args()
    with connect() as conn:
        rows = fetch_prices(conn, args.ticker.upper(), as_of=args.as_of.isoformat())
    prices = [float(r['adj_close']) for r in rows]
    if len(prices) < 2200:
        raise ValueError('need 2200+ closes for separated calibration/evaluation folds')
    if not all(p > 0 for p in prices):
        raise ValueError('invalid adjusted close')
    raw = json.dumps(rows, sort_keys=True, default=str)
    result = {'ticker': args.ticker.upper(), 'requested_as_of': str(args.as_of),
              'last_price_date': rows[-1]['date'], 'model_version': MODEL_VERSION,
              'input_sha256': hashlib.sha256(raw.encode()).hexdigest(),
              'role': 'diagnostic; no production promotion',
              'validation': validate(log_returns(prices), evaluate_lstm=True)}
    Path(args.output).write_text(json.dumps(result, indent=2, default=str)+'\n')
    print(json.dumps({'output': args.output, 'verdict': result['validation']['verdict']}))


if __name__ == '__main__':
    main()
