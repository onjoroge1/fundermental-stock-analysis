"""Reviewed GAAP facts from original SEC-filed earnings releases.

This is a bounded evidence registry, not an automatic HTML table guesser.
New periods require review of quarter, EPS basis, currency and share basis.
The source hash identifies the reviewed document, not a retrieval timestamp.
"""
from datetime import date, timedelta
from functools import lru_cache
import json
import math
from pathlib import Path


@lru_cache(maxsize=1)
def reviewed_eps() -> tuple[dict, ...]:
    rows = json.loads(Path(__file__).with_name('earnings_release_eps.json').read_text())
    seen = set()
    for r in rows:
        key = (r['cik'], r['period_start'], r['period_end'], r['field'])
        if key in seen:
            raise ValueError('Duplicate reviewed earnings fact')
        seen.add(key)
        days = (date.fromisoformat(r['period_end']) - date.fromisoformat(r['period_start'])).days
        if (r['basis'] != 'US_GAAP' or r['currency'] != 'USD'
                or r['unit'] != 'USD/share' or r['field'] != 'diluted_eps'
                or not 60 <= days <= 115 or not math.isfinite(r['value'])
                or abs(r['value']) > 10_000 or r['published_date'] < r['period_end']
                or not r['source_url'].startswith('https://www.sec.gov/Archives/edgar/data/')):
            raise ValueError('Invalid reviewed earnings fact')
    return tuple(rows)


def supplement_quarters(cik, quarters: list[dict], events: list[dict]) -> None:
    """Fill only missing, exact-entity/period facts without advancing availability."""
    for r in reviewed_eps():
        if str(r['cik']).zfill(10) != str(cik).zfill(10):
            continue
        for p in quarters:
            if (p['period_start'], p['period_end']) != (r['period_start'], r['period_end']):
                continue
            field = r['field']
            if p['fields'].get(field) is not None:
                if p['fields'][field] != r['value']:
                    events.append({'event': 'EARNINGS_RELEASE_EPS_CONFLICT',
                                   'period_end': p['period_end'], 'reviewed_source': dict(r),
                                   'existing_value': p['fields'][field]})
                continue
            p['fields'][field] = r['value']
            p['field_sources'][field] = r['accession']
            # Date-only SEC disclosures are eligible from the following day.
            available = (date.fromisoformat(r['published_date']) + timedelta(days=1)).isoformat()
            p['filed_at'] = max(p['filed_at'], r['published_date'])
            p['available_at'] = max(p['available_at'], available)
            events.append({'event': 'EARNINGS_RELEASE_EPS_RECOVERED',
                           'period_end': p['period_end'], 'reviewed_source': dict(r)})


def eps_provenance(period: dict) -> dict:
    """Expose the original exhibit, basis and hash after database round-trip."""
    return {r['field']: dict(r) for r in reviewed_eps()
            if period['period_end'] == r['period_end']
            and period.get('field_sources', {}).get(r['field']) == r['accession']
            and period['fields'].get(r['field']) == r['value']}
