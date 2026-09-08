"""Validate a licensed canonical export; --apply imports the validated batch."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stock_machine.ingestion.estimate_archives import import_archive, validate_archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--original-file', type=Path,
                        help='Original licensed export; required for --apply')
    args = parser.parse_args()
    archive = json.loads(args.archive.read_text())
    rows = validate_archive(archive)
    report = {'mode': 'validation', 'rows': len(rows),
              'tickers': sorted({r['ticker'] for r in rows}),
              'first_available_at': min(r['observed_at'] for r in rows),
              'last_available_at': max(r['observed_at'] for r in rows)}
    if args.apply:
        if not args.original_file or hashlib.sha256(args.original_file.read_bytes()).hexdigest() != archive['original_file_sha256']:
            raise ValueError('--apply requires the original vendor export with the recorded SHA-256')
        from stock_machine import db
        with db.connect() as conn:
            report.update(import_archive(conn, archive), mode='import')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
