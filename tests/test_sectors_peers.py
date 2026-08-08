from stock_machine.peers import percentile_rank
from stock_machine.sectors import UNIVERSE, classify


def test_classify_known_sics():
    assert classify("3674") == "Semiconductors"        # NVDA, AMD, INTC
    assert classify("3559") == "Semiconductors"        # AMAT, LRCX
    assert classify("3571") == "Technology Hardware"   # AAPL, DELL
    assert classify("7372") == "Software & Internet"   # MSFT, ORCL
    assert classify("7370") == "Software & Internet"   # GOOGL, META
    assert classify("7841") == "Software & Internet"   # NFLX
    assert classify("3711") == "Automobiles"           # TSLA, F, GM
    assert classify("5961") == "Consumer & Retail"     # AMZN
    assert classify("5331") == "Consumer & Retail"     # WMT, TGT
    assert classify(None) == "Unclassified"
    assert classify("6022") == "Banks & Consumer Finance"  # bank adapter v1
    assert classify("6199") == "Banks & Consumer Finance"  # SOFI


def test_specific_beats_broad():
    # 3674 sits inside 3600-3699 but must classify as Semiconductors,
    # not Technology Hardware
    assert classify(3674) == "Semiconductors"


def test_universe_has_no_duplicates():
    assert len(UNIVERSE) == len(set(UNIVERSE))


def test_percentile_rank():
    vals = [10, 20, 30, 40, 50]
    assert percentile_rank(vals, 50) == 90.0   # above 4 of 5, tie with self
    assert percentile_rank(vals, 10) == 10.0
    assert percentile_rank(vals, 30) == 50.0
