from pathlib import Path

from stock_machine.webapp_ops import app


def test_p1_api_route_is_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/p1/{ticker}" in paths


def test_prediction_lab_overlay_is_loaded_after_base_app():
    root = Path(__file__).resolve().parent.parent
    html = (root / "webui" / "index.html").read_text()
    assert '/ui/app.js' in html
    assert '/ui/p1_predict.js' in html
    assert html.index('/ui/app.js') < html.index('/ui/p1_predict.js')
    assert '/ui/p1.css' in html
