"""Learned-direction challenger: frozen at decision time, judged by a fixed protocol."""

import pytest

from stock_machine.agent_intelligence import bandit, direction
from stock_machine.market_calendar import session_offset


def state(bias_direction="BULLISH", fundamental=0.8):
    return {
        "direction": bias_direction,
        "signal_components": {"fundamental": fundamental, "technical": 0.2},
        "technical": {"features": {"realized_vol_20": 0.3}},
    }


def trained(reward, n=200):
    x = bandit.context_vector(state())
    arm = bandit.empty_arm(len(x))
    for _ in range(n):
        arm = bandit.update(arm, x, reward)
    return arm


@pytest.mark.parametrize(
    "value,expected", [("BULLISH", "LONG"), ("BEARISH", "SHORT"), ("NEUTRAL", "FLAT"), (None, "FLAT")]
)
def test_incumbent_is_the_heuristic_direction(value, expected):
    assert direction.incumbent({"direction": value}) == expected


def test_cold_start_challenger_is_flat_and_never_acts():
    value = direction.challenger(state(), {}, model_sequence=None)
    assert value["challenger"] == "FLAT" and value["incumbent"] == "LONG"
    assert value["acts_on_paper"] is False
    assert value["protocol_sha256"] == direction.PROTOCOL_SHA256


def test_challenger_can_disagree_with_the_heuristic():
    arms = {"LONG_STOCK": trained(-0.4), "SHORT_STOCK": trained(0.4)}
    value = direction.challenger(state("BULLISH"), arms, model_sequence=7)
    assert value["incumbent"] == "LONG" and value["challenger"] == "SHORT"
    assert value["model_sequence"] == 7
    assert direction.challenger(state(), {"LONG_STOCK": trained(-0.4), "SHORT_STOCK": trained(-0.4)},
                                model_sequence=7)["challenger"] == "FLAT"


def rows(blocks, per_block, *, inc, ch, long_reward, short_reward, start="2026-10-01"):
    out = []
    for b in range(blocks):
        session = session_offset(start, b * direction.PROTOCOL["block_sessions"])
        for _ in range(per_block):
            out.append({"execution_session": session, "incumbent": inc, "challenger": ch,
                        "rewards": {"LONG": long_reward(b), "SHORT": short_reward(b)}})
    return out


def test_fewer_than_minimum_blocks_is_pending_however_many_decisions():
    # Thousands of decisions in a few blocks are still few independent units.
    value = direction.evaluate(rows(3, 500, inc="LONG", ch="SHORT",
                                    long_reward=lambda b: -1, short_reward=lambda b: 1))
    assert value["status"] == "PENDING_EVIDENCE"
    assert value["blocks"] == 3 and value["decisions"] == 1500
    assert value["promotion"] == "NOT_AUTHORIZED"


def test_consistently_better_challenger_passes_only_to_review():
    value = direction.evaluate(rows(12, 5, inc="LONG", ch="SHORT",
                                    long_reward=lambda b: -0.2 - 0.05 * (b % 3),
                                    short_reward=lambda b: 0.2 + 0.05 * (b % 3)))
    assert value["status"] == "PASS_REQUIRES_INDEPENDENT_REVIEW"
    assert value["lower_95pct_paired_difference"] > 0
    assert value["direction_agreement"] == 0
    assert value["trade_qualification"] is False


def test_worse_or_noisy_challenger_is_not_superior():
    worse = direction.evaluate(rows(12, 5, inc="LONG", ch="FLAT",
                                    long_reward=lambda b: 0.3, short_reward=lambda b: -0.3))
    assert worse["status"] == "NOT_SUPERIOR" and worse["mean_paired_difference"] < 0
    noisy = direction.evaluate(rows(12, 5, inc="LONG", ch="SHORT",
                                    long_reward=lambda b: (-1) ** b, short_reward=lambda b: -(-1) ** b))
    assert noisy["status"] == "NOT_SUPERIOR"


def test_flat_scores_zero_and_requires_positive_challenger_reward():
    # Abstaining beats a losing heuristic, but a zero-reward challenger cannot pass.
    value = direction.evaluate(rows(12, 5, inc="LONG", ch="FLAT",
                                    long_reward=lambda b: -0.3, short_reward=lambda b: 0.0))
    assert value["mean_paired_difference"] == pytest.approx(0.3)
    assert value["lower_95pct_paired_difference"] > 0
    assert value["challenger_mean_reward"] == 0
    assert value["status"] == "NOT_SUPERIOR"


def test_sessions_before_the_epoch_are_rejected():
    with pytest.raises(ValueError, match="BEFORE_EPOCH"):
        direction.evaluate(rows(1, 1, inc="LONG", ch="LONG", long_reward=lambda b: 0,
                                short_reward=lambda b: 0, start="2026-09-01"))


def test_protocol_is_frozen_by_hash():
    from stock_machine.agents.contracts import digest

    assert direction.PROTOCOL_SHA256 == digest(direction.PROTOCOL)
    changed = {**direction.PROTOCOL, "minimum_blocks": 6}
    assert direction.evaluate([], changed)["protocol_sha256"] != direction.PROTOCOL_SHA256
