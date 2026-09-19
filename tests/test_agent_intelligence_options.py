from datetime import date, timedelta
from stock_machine.agent_intelligence import option_bridge


def test_expiration_selects_bounded_near_target():
    d=date.today()+timedelta(days=44)
    rows=[{"month":"X","standard":d.strftime("%Y%m%d")}]
    value=option_bridge._expiration(rows)
    assert value["dte"]==44


def test_direction_types_are_defined_risk_templates_only():
    names={x.value for x in option_bridge.DIRECTION_TYPES["BULLISH"]}
    assert names=={"bull_call_debit_spread","bull_put_credit_spread"}
    names={x.value for x in option_bridge.DIRECTION_TYPES["BEARISH"]}
    assert names=={"bear_put_debit_spread","bear_call_credit_spread"}
    assert {x.value for x in option_bridge.DIRECTION_TYPES["NEUTRAL"]}=={"iron_condor"}
