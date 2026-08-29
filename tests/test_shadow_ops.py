from stock_machine.webapp_ops import app


def test_alpha_shadow_endpoint_is_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/alpha-shadow" in paths
