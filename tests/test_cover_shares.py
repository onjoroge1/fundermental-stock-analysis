import pytest

from stock_machine.ingestion.cover_shares import extract_cover_shares, supplement_companyfacts
from stock_machine.normalization.financial_periods import extract_shares_outstanding


def filing(classes, *, dimension='us-gaap:StatementClassOfStockAxis', instant='2026-07-15', cik='0000012345'):
    contexts = []
    facts = []
    for i, (member, value, attrs) in enumerate(classes):
        segment = f'<segment><d:explicitMember dimension="{dimension}">{member}</d:explicitMember></segment>' if member else ''
        contexts.append(f'<context id="c{i}"><entity><identifier>{cik}</identifier>{segment}</entity><period><instant>{instant}</instant></period></context>')
        facts.append(f'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c{i}" unitRef="shares" {attrs}>{value}</ix:nonFraction>')
    return '<root xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:d="http://xbrl.org/2006/xbrldi">' + ''.join(contexts) + '<unit id="shares"><measure>xbrli:shares</measure></unit>' + ''.join(facts) + '</root>'


def parse(content):
    return extract_cover_shares(content, cik='0000012345', filed='2026-07-23', accession='test-2026', form='10-Q')


def test_class_totals_include_capital_class_and_zero_with_causal_availability():
    doc = filing([('us-gaap:CommonClassAMember', '5,868', 'scale="6"'),
                  ('goog:CapitalClassCMember', '5,527', 'scale="6"'),
                  ('us-gaap:CommonClassBMember', '835', 'scale="6"'),
                  ('custom:CommonClassHMember', 'no', 'format="ixt:fixed-zero"')])
    entry = parse(doc)
    assert entry['val'] == 12_230_000_000
    rows = extract_shares_outstanding({'facts': {'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': [entry]}}}}})
    assert rows[0]['available_at'] == '2026-07-24'


@pytest.mark.parametrize('classes,dimension', [
    ([('class:A', '10', ''), ('class:A', '11', '')], 'us-gaap:StatementClassOfStockAxis'),
    ([(None, '99', ''), ('class:A', '10', '')], 'us-gaap:StatementClassOfStockAxis'),
    ([('class:A', '10', '')], 'us-gaap:StatementBusinessSegmentsAxis'),
    ([('class:A', '-10', '')], 'us-gaap:StatementClassOfStockAxis'),
])
def test_ambiguous_or_invalid_shares_are_rejected(classes, dimension):
    with pytest.raises(ValueError):
        parse(filing(classes, dimension=dimension))


def test_duplicate_identical_facts_are_not_double_counted_and_future_facts_excluded():
    assert parse(filing([('class:A', '10', ''), ('class:A', '10', '')]))['val'] == 10
    assert parse(filing([(None, '10', '')], instant='2026-08-01')) is None


def test_source_failure_preserves_original_companyfacts(monkeypatch):
    from stock_machine.ingestion import sec
    def unavailable(url):
        raise TimeoutError('source unavailable')
    monkeypatch.setattr(sec, '_get', unavailable)
    facts = {'facts': {'us-gaap': {}}}
    sub = {'filings': {'recent': {'form': ['10-Q'], 'filingDate': ['2026-07-23'],
                                'accessionNumber': ['test-2026'], 'primaryDocument': ['test.htm']}}}
    events = supplement_companyfacts('TEST', '0000012345', sub, facts)
    assert events[0]['event'] == 'COVER_SHARES_UNAVAILABLE'
    assert facts == {'facts': {'us-gaap': {}}}
