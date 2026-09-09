import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def capture_module():
    path = Path(__file__).parents[1] / "scripts" / "capture_option_surfaces.py"
    spec = importlib.util.spec_from_file_location("capture_option_surfaces", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_meaningful_surface_requires_positive_atm_iv(capture_module):
    assert capture_module._meaningful({
        "status": "OK", "features": {"atm_iv": 0.31}
    })
    assert not capture_module._meaningful({
        "status": "OK", "features": {"atm_iv": None, "has_oi": 1.0}
    })
    assert not capture_module._meaningful({
        "status": "OK", "features": {"atm_iv": True}
    })
    assert not capture_module._meaningful({
        "status": "PENDING", "features": {"atm_iv": 0.31}
    })


def test_arguments_reject_negative_minimum(capture_module):
    with pytest.raises(SystemExit):
        capture_module._arguments(["--min-successes", "-1"])


def test_arguments_accept_session_and_minimum(capture_module):
    args = capture_module._arguments(
        ["--require-session", "--min-successes", "8"]
    )
    assert args.require_session is True
    assert args.min_successes == 8
