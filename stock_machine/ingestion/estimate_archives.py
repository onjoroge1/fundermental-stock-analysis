"""Validate/import licensed PIT consensus exports; never backdate current FMP.

The interchange format is documented in docs/EARNINGS_AND_ESTIMATE_RECOVERY.md.
This is not a proprietary FactSet/IBES file decoder or an entitlement claim.
"""
from datetime import date, datetime, timezone
import hashlib
import json
import math
import re

from psycopg.types.json import Jsonb


def timestamp(value):
    parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Archive timestamps must include time and timezone')
    return parsed.astimezone(timezone.utc)


def validate_archive(archive: dict, *, now=None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    if archive.get('format') != 'pit-consensus-v1' or archive.get('point_in_time') is not True:
        raise ValueError('A genuine point-in-time archive is required')
    for key in ('provider', 'dataset'):
        if not re.fullmatch(r'[a-z0-9_-]+', archive.get(key, '')):
            raise ValueError(f'Invalid archive {key}')
    if archive['provider'] not in ('factset', 'lseg_ibes'):
        raise ValueError('Unreviewed archive provider')
    for key in ('license_reference', 'source_reference', 'timestamp_methodology'):
        if not isinstance(archive.get(key), str) or not archive[key].strip():
            raise ValueError(f'Missing archive {key}')
    if not re.fullmatch(r'[a-f0-9]{64}', archive.get('original_file_sha256', '')):
        raise ValueError('Original vendor export hash is required')
    retrieved = timestamp(archive['retrieved_at'])
    if retrieved > now:
        raise ValueError('Future archive retrieval date')
    if not isinstance(archive.get('records'), list) or not archive['records']:
        raise ValueError('Empty archive')
    rows, seen = [], {}
    for r in archive['records']:
        snapshot, available = timestamp(r['snapshot_at']), timestamp(r['available_at'])
        if not snapshot <= available <= retrieved:
            raise ValueError('Availability must follow the vendor snapshot and precede retrieval')
        end = date.fromisoformat(r['forecast_period_end'])
        if r['period_type'] not in ('annual', 'quarter'):
            raise ValueError('Fiscal annual/quarter period required')
        if (r.get('currency') != 'USD' or r.get('eps_basis') not in ('gaap', 'normalized')
                or r.get('share_basis') != 'contemporaneous' or r.get('eps_type') != 'diluted'):
            raise ValueError('Unsupported currency/EPS/share basis')
        if (not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}', r.get('ticker', ''))
                or not str(r.get('cik', '')).isdigit() or not 0 < int(r['cik']) < 10**10
                or not isinstance(r.get('security_id'), str) or not r['security_id'].strip()):
            raise ValueError('Ticker, SEC CIK and original provider security ID are required')
        count = r.get('analyst_count')
        if type(count) is not int or count < 1:
            raise ValueError('Positive analyst count required')
        payload = {'analyst_count': count}
        for name in ('eps_mean', 'eps_low', 'eps_high', 'revenue_mean', 'revenue_low', 'revenue_high'):
            v = r.get(name)
            if v is not None and (type(v) not in (int, float) or not math.isfinite(v)):
                raise ValueError('Non-finite/non-numeric consensus value')
            if v is not None and name.startswith('eps') and abs(v) > 10_000:
                raise ValueError('Invalid EPS scale')
            if v is not None and name.startswith('revenue') and v < 0:
                raise ValueError('Invalid revenue')
            payload[name] = v
        if payload['eps_mean'] is None:
            raise ValueError('EPS consensus mean required')
        if any(r.get(k) is not None for k in ('revenue_mean', 'revenue_low', 'revenue_high')) and r.get('revenue_unit') != 'USD':
            raise ValueError('Revenue must be converted explicitly to USD, not millions')
        for metric in ('eps', 'revenue'):
            low, mean, high = (payload[f'{metric}_{s}'] for s in ('low', 'mean', 'high'))
            if ((low is not None and high is not None and low > high)
                    or (low is not None and mean is not None and low > mean)
                    or (high is not None and mean is not None and mean > high)):
                raise ValueError('Consensus range is inconsistent')
        # A basis-specific stream prevents revisions across GAAP/normalized EPS
        # or a provider's changing company/security mapping.
        source = ':'.join(('archive', archive['provider'], archive['dataset'],
                           r['eps_basis'], 'USD', 'diluted', r['security_id']))
        payload.update(snapshot_at=snapshot.isoformat(), currency='USD',
                       eps_basis=r['eps_basis'], share_basis=r['share_basis'],
                       security_id=r['security_id'], cik=str(int(r['cik'])))
        row = dict(ticker=r['ticker'], source=source, observed_at=available.isoformat(),
                   period_type=r['period_type'], forecast_period_end=end.isoformat(), payload=payload)
        identity = tuple(row[k] for k in ('ticker', 'source', 'observed_at', 'period_type', 'forecast_period_end'))
        if identity in seen and seen[identity] != payload:
            raise ValueError('Conflicting duplicate archive observation')
        if identity not in seen:
            rows.append(row)
        seen[identity] = payload
    return rows


def import_archive(conn, archive: dict) -> dict:
    """One atomic batch; immutable observations and replay-safe audit evidence."""
    rows = validate_archive(archive)
    digest = hashlib.sha256(json.dumps(archive, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    inserted = 0
    with conn.transaction():
        with conn.cursor() as cur:
            for r in rows:
                cur.execute('SELECT cik FROM companies WHERE ticker=%s', (r['ticker'],))
                company = cur.fetchone()
                if not company or str(int(company[0])) != r['payload']['cik']:
                    raise ValueError('Archive company identity does not match the registered universe')
                identity = tuple(r[k] for k in ('ticker', 'source', 'observed_at', 'period_type', 'forecast_period_end'))
                cur.execute('''INSERT INTO consensus_vintages
                    (ticker,source,observed_at,period_type,forecast_period_end,payload)
                    VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING''', (*identity, Jsonb(r['payload'])))
                inserted += cur.rowcount
                if not cur.rowcount:
                    cur.execute('''SELECT payload FROM consensus_vintages WHERE
                        ticker=%s AND source=%s AND observed_at=%s AND period_type=%s AND forecast_period_end=%s''', identity)
                    if cur.fetchone()[0] != r['payload']:
                        raise ValueError('Archive conflicts with a persisted observation; batch rolled back')
            cur.execute('''INSERT INTO consensus_archive_imports (archive_sha256, archive, row_count)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING''', (digest, Jsonb(archive), len(rows)))
    return {'archive_sha256': digest, 'validated': len(rows), 'inserted': inserted}
