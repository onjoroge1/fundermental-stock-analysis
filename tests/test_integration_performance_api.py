from uuid import uuid4
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from stock_machine.integrations import performance_api as api
from stock_machine.admin_panel import store
from stock_machine.admin_panel.security import PanelError


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('VERCEL', raising=False)
    app = FastAPI(); app.include_router(api.router)
    return TestClient(app, base_url='https://testserver')


def test_performance_requires_existing_owner_session_before_data_access(client, monkeypatch):
    def missing(*args, **kw): raise PanelError('LOGIN_REQUIRED',401)
    monkeypatch.setattr(store,'session',missing)
    monkeypatch.setattr(api.service,'read_view',lambda *args: pytest.fail('Unauthorized read'))
    assert client.get('/api/operator/performance').status_code == 401
    assert client.post('/api/operator/performance/reports',json={}).status_code == 401


def test_read_does_not_queue_or_generate_reports(client,monkeypatch):
    monkeypatch.setattr(store,'session',lambda *a,**k:{'username':'admin'})
    monkeypatch.setattr(api.service,'read_view',lambda ticker:{'ticker':ticker,'jobs':[]})
    monkeypatch.setattr(api.service,'enqueue_report',lambda *a:pytest.fail('Read queued work'))
    r = client.get('/api/operator/performance?ticker=MSFT')
    assert r.status_code == 200 and r.json()['ticker'] == 'MSFT'
    assert r.headers['cache-control'] == 'no-store'
    assert client.get('/api/operator/performance?ticker=UNKNOWN').status_code == 400


def test_report_write_checks_origin_csrf_and_exact_payload(client,monkeypatch):
    calls=[]
    def session(raw,**kw):
        if kw.get('write') and kw.get('csrf') != 'test-csrf': raise PanelError('CSRF_REJECTED',403)
        return {'username':'admin'}
    monkeypatch.setattr(store,'session',session)
    monkeypatch.setattr(api.service,'enqueue_report',lambda key:calls.append(key) or {'status':'PENDING'})
    key=str(uuid4()); valid={'request_id':key}
    assert client.post('/api/operator/performance/reports',json=valid).status_code == 403
    headers={'Origin':'https://testserver','X-CSRF-Token':'test-csrf'}
    assert client.post('/api/operator/performance/reports',headers=headers,json={**valid,'mode':'LIVE'}).status_code == 400
    r=client.post('/api/operator/performance/reports',headers=headers,json=valid)
    assert r.status_code == 202 and calls == [key]


def test_report_download_is_attachment_with_inert_policy(client,monkeypatch):
    monkeypatch.setattr(store,'session',lambda *a,**k:{'username':'admin'})
    monkeypatch.setattr(api.service,'report_html',lambda key:'<h1>Report</h1>')
    r=client.get('/api/operator/performance/reports/job_test/html')
    assert r.status_code == 200
    assert 'attachment' in r.headers['content-disposition']
    assert 'sandbox' in r.headers['content-security-policy']


def test_performance_ui_has_no_html_injection_or_credential_storage():
    from pathlib import Path
    root=Path(__file__).parents[1]
    js=(root/'webui/performance.js').read_text()
    for bad in ('innerHTML','localStorage','sessionStorage','document.cookie'):
        assert bad not in js
    assert 'textContent' in js
    assert 'disconnect()' in js and 'chart.remove()' in js
