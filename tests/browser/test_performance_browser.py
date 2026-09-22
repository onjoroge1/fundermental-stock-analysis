"""Actual Lightweight Charts smoke test; fixture API, never production accounts."""
import os
import json
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest

pytestmark = pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1', reason='Dedicated browser CI only')
ROOT = Path(__file__).resolve().parents[2]


def fixture_view():
    return {'session':'2020-01-06','portfolio_id':'legacy-agent-paper-v1','current':{'equity_usd':'100900','net_pnl_usd':'900','status':'COMPLETE'},
        'jobs':[], 'limitations':['Legacy fixture data; not production results.'],
        'intelligence':{'status':'NOT_RUN'},
        'latest_report':{'status':'OK','quantstats':{'status':'OK'},'series':{'risk_metrics_status':'INSUFFICIENT_HISTORY','points':[
            {'time':'2020-01-02','value':100000,'drawdown':0},
            {'time':'2020-01-03','value':100400,'drawdown':0},
            {'time':'2020-01-06','value':100900,'drawdown':0}]},
            'benchmark':{'status':'OK','points':[{'time':'2020-01-02','value':100000},{'time':'2020-01-03','value':100200},{'time':'2020-01-06','value':100700}]}},
        'replay':{'ticker':'AAPL','prices':[{'time':'2020-01-02','value':100},{'time':'2020-01-03','value':101},{'time':'2020-01-06','value':102}],
            'events':[{'event_id':'d1','decision_id':'d1','kind':'RESEARCH_DECISION','chart_day':'2020-01-03',
                       'occurred_at':'2020-01-03T22:00:00Z','recorded_at':'2020-01-03T22:00:01Z','status':'RECORDED',
                       'rationale':'Original fixture rationale, not inferred later.','price_date_basis':'2020-01-03'}]}}


@pytest.fixture
def server():
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw): super().__init__(*a,directory=str(ROOT/'webui'),**kw)
        def do_GET(self):
            if self.path == '/performance': self.path='/performance.html'
            elif self.path.startswith('/ui/'): self.path=self.path[3:]
            return super().do_GET()
        def log_message(self,*args): pass
    s=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    t=Thread(target=s.serve_forever,daemon=True);t.start()
    yield f'http://127.0.0.1:{s.server_port}'
    s.shutdown();s.server_close();t.join()


def test_actual_charts_and_replay_desktop_mobile(server,tmp_path):
    from playwright.sync_api import sync_playwright
    assert (ROOT/'webui/vendor/lightweight-charts-5.0.9.js').exists(), 'Run build_chart_assets first'
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1280,'height':900})
        errors=[];writes=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            if route.request.method != 'GET': writes.append(route.request.url)
            body={'authenticated':True,'csrf_token':'test'} if route.request.url.endswith('/session') else fixture_view()
            route.fulfill(content_type='application/json',body=json.dumps(body))
        page.route('**/api/operator/**',api)
        page.goto(server+'/performance')
        page.wait_for_selector('#events button')
        assert page.evaluate('LightweightCharts.version()') == '5.0.9'
        assert page.locator('canvas').count() >= 3
        page.locator('#events button').first.click()
        assert 'Original fixture rationale' in page.locator('#event-detail').inner_text()
        assert writes == [] and errors == []
        page.screenshot(path=str(tmp_path/'performance-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.wait_for_function('document.documentElement.scrollWidth <= 390')
        assert page.evaluate('document.documentElement.scrollWidth') <= 390
        page.screenshot(path=str(tmp_path/'performance-mobile.png'),full_page=True)
        assert errors == []
        browser.close()
