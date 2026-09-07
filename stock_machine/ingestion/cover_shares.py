"""Recover cover-page common shares omitted by entity-wide CompanyFacts.

Only exact DEI share facts are accepted. Share-class totals require one
unambiguous value per common class, one instant, one entity and shares units.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET


def extract_cover_shares(content: str, *, cik: str, filed: str, accession: str, form: str) -> dict | None:
    root = ET.fromstring(content)
    local = lambda name: name.rsplit('}', 1)[-1]
    contexts = {e.attrib['id']: e for e in root.iter() if local(e.tag) == 'context'}
    units = {e.attrib['id']: [x.text for x in e.iter() if local(x.tag) == 'measure']
             for e in root.iter() if local(e.tag) == 'unit'}
    groups = defaultdict(lambda: defaultdict(set))
    invalid = set()
    for node in root.iter():
        if local(node.tag) != 'nonFraction' or node.attrib.get('name') != 'dei:EntityCommonStockSharesOutstanding':
            continue
        context = contexts.get(node.attrib.get('contextRef'))
        if context is None:
            raise ValueError('Cover share fact has no context')
        instants = [e.text for e in context.iter() if local(e.tag) == 'instant']
        identifiers = [e.text for e in context.iter() if local(e.tag) == 'identifier']
        if len(instants) != 1 or len(identifiers) != 1 or identifiers[0].lstrip('0') != cik.lstrip('0'):
            raise ValueError('Ambiguous cover share entity or instant')
        instant = instants[0]
        if date.fromisoformat(instant) > date.fromisoformat(filed):
            continue
        members = [e for e in context.iter() if local(e.tag) in ('explicitMember', 'typedMember')]
        if len(members) > 1 or any(local(e.tag) != 'explicitMember'
               or e.attrib.get('dimension') != 'us-gaap:StatementClassOfStockAxis'
               or not e.text for e in members):
            invalid.add(instant)
            continue
        member = members[0].text if members else '__total__'
        measure = units.get(node.attrib.get('unitRef'), [])
        if len(measure) != 1 or measure[0].split(':')[-1] != 'shares':
            invalid.add(instant)
            continue
        try:
            fmt = node.attrib.get('format', '').split(':')[-1]
            if fmt in ('fixed-zero', 'numdash'):
                value = Decimal(0)
            elif fmt in ('num-dot-decimal', 'numdotdecimal', ''):
                value = Decimal(''.join(node.itertext()).replace(',', '').strip())
            else:
                raise ValueError('Unsupported numeric format')
            value *= Decimal(10) ** int(node.attrib.get('scale', '0'))
            if node.attrib.get('sign') == '-':
                value = -value
            if not value.is_finite() or value < 0 or value != value.to_integral_value():
                raise ValueError('Invalid share count')
            groups[instant][member].add(int(value))
        except (ValueError, InvalidOperation):
            invalid.add(instant)
    if invalid and (not groups or max(invalid) >= max(groups)):
        raise ValueError('Invalid cover-page share context or value')
    if not groups:
        return None
    if len(groups) != 1:
        raise ValueError('Cover share classes do not share one instant')
    instant = max(groups)
    values = groups[instant]
    if instant in invalid or any(len(v) != 1 for v in values.values()):
        raise ValueError('Ambiguous cover-page share values')
    counts = {k: next(iter(v)) for k, v in values.items()}
    total = counts.get('__total__', sum(counts.values()))
    if '__total__' in counts and len(counts) > 1 and total != sum(v for k, v in counts.items() if k != '__total__'):
        raise ValueError('Cover-page total disagrees with classes')
    if total <= 0:
        return None
    return {'end': instant, 'val': total, 'filed': filed, 'accn': accession,
            'form': form, 'cover_page_components': counts,
            'provenance': 'SEC inline XBRL cover-page common shares'}


def supplement_companyfacts(ticker: str, cik: str, submissions: dict, facts: dict) -> list[dict]:
    from .sec import _get
    from ..provenance import save_raw
    from ..normalization.financial_periods import extract_shares_outstanding
    if extract_shares_outstanding(facts):
        return []
    recent = submissions.get('filings', {}).get('recent', {})
    candidates = [i for i, form in enumerate(recent.get('form', []))
                  if form in ('10-Q', '10-K') and recent['filingDate'][i] <= date.today().isoformat()]
    if not candidates:
        return []
    i = max(candidates, key=lambda n: recent['filingDate'][n])
    accession = recent['accessionNumber'][i]
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{recent['primaryDocument'][i]}"
    try:
        response = _get(url)
        entry = extract_cover_shares(response.text, cik=cik, filed=recent['filingDate'][i],
                                     accession=accession, form=recent['form'][i])
        save_raw('sec', [ticker, 'cover_share_filing', accession], {'url': url, 'html': response.text}, url)
        if entry is None:
            return []
        entry['source_url'] = url
        facts.setdefault('facts', {}).setdefault('dei', {})['EntityCommonStockSharesOutstanding'] = {
            'units': {'shares': [entry]}}
        return [{'event': 'COVER_SHARES_RECOVERED', 'dataset': 'shares', 'source_url': url,
                 'as_of': entry['end'], 'shares': entry['val'], 'components': entry['cover_page_components']}]
    except Exception as exc:
        return [{'event': 'COVER_SHARES_UNAVAILABLE', 'dataset': 'shares',
                 'source_url': url, 'detail': type(exc).__name__}]
