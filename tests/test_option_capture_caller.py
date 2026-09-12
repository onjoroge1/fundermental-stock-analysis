import importlib.util
from pathlib import Path
from unittest.mock import Mock
import httpx
import pytest


@pytest.fixture
def caller(monkeypatch):
    spec = importlib.util.spec_from_file_location('caller', Path(__file__).parents[1]/'scripts/capture_option_surfaces_api.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setenv('STOCK_MACHINE_ADMIN_TOKEN','s'*32)
    monkeypatch.setenv('P1_OPTION_TICKERS','AAPL,MSFT')
    monkeypatch.setenv('P1_OPTION_MIN_SUCCESSES','2')
    monkeypatch.delenv('STOCK_MACHINE_API_BASE_URL',raising=False)
    return m


@pytest.mark.parametrize('failure', [False, True])
def test_api_caller_requires_persisted_success(caller, monkeypatch, failure):
    def request(req):
        assert req.headers['Authorization'] == 'Bearer '+'s'*32
        ticker = req.url.path.split('/')[-2]
        if failure and ticker == 'MSFT':
            return httpx.Response(503,json={'detail':'unavailable'})
        return httpx.Response(200,json={'status':'ok','ticker':ticker,'snapshot_id':'saved','as_of':'2026-09-12'})
    client = httpx.Client(transport=httpx.MockTransport(request),headers={'Authorization':'Bearer '+'s'*32})
    monkeypatch.setattr(caller.httpx,'Client',lambda **kw: client)
    assert caller.main() == int(failure)


def test_auth_failure_stops_without_logging_body(caller,monkeypatch,capsys):
    handler = Mock(return_value=httpx.Response(401,text='private-response'))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(caller.httpx,'Client',lambda **kw: client)
    assert caller.main() == 1
    assert handler.call_count == 1
    assert 'private-response' not in capsys.readouterr().out


def test_missing_secret_never_sends_request(caller,monkeypatch):
    monkeypatch.delenv('STOCK_MACHINE_ADMIN_TOKEN')
    with pytest.raises(ValueError,match='missing'):
        caller.main()
