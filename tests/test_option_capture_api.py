from contextlib import nullcontext
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from stock_machine.webapp_ops import app
from stock_machine.options import capture
import stock_machine.market_data as market

TOKEN = 'a' * 32


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('STOCK_MACHINE_ADMIN_TOKEN', TOKEN)
    provider = Mock()
    provider.session_status.return_value = SimpleNamespace(connected=True, authenticated=True, competing=False)
    factory = Mock(return_value=provider)
    monkeypatch.setattr(market, 'get_provider', factory)
    return TestClient(app), provider, factory


@pytest.mark.parametrize('token', [None, 'wrong'])
def test_auth_precedes_provider_access(setup, token):
    client, _, factory = setup
    response = client.post('/api/admin/options/AAPL/capture', headers={} if token is None else {'Authorization': 'Bearer '+token})
    assert response.status_code == 401
    factory.assert_not_called()


def test_authenticated_request_collects_and_persists(setup, monkeypatch):
    client, provider, _ = setup
    provider.resolve_underlying.return_value = SimpleNamespace(option_months=['SEP26','OCT26','NOV26'])
    provider.quote_underlying.return_value = SimpleNamespace(mark=100)
    provider.available_strikes.return_value = SimpleNamespace(call_strikes=list(range(80,121)), put_strikes=[])
    provider.option_chain.return_value = SimpleNamespace(fetched_at=datetime.now(timezone.utc))
    monkeypatch.setattr(capture.db, 'connect', lambda: nullcontext(object()))
    monkeypatch.setattr(capture, 'history', lambda *a, **kw: [])
    surface = {'status':'OK', 'as_of':'2026-09-12T10:00:00Z', 'features':{'atm_iv':0.3}}
    monkeypatch.setattr(capture, 'extract_surface', lambda *a, **kw: surface)
    save = Mock(return_value='persisted-id')
    monkeypatch.setattr(capture, 'save', save)
    response = client.post('/api/admin/options/aapl/capture', headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code == 200
    assert response.json()['snapshot_id'] == 'persisted-id'
    assert response.json()['ticker'] == 'AAPL'
    assert provider.option_chain.call_count == 2
    assert all(len(c.args[2]) == 18 for c in provider.option_chain.call_args_list)
    save.assert_called_once()
    provider.close.assert_called_once()


def test_unready_session_never_collects(setup):
    client, provider, _ = setup
    provider.session_status.return_value.authenticated = False
    response = client.post('/api/admin/options/AAPL/capture', headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code == 503
    provider.resolve_underlying.assert_not_called()
    provider.close.assert_called_once()


def test_provider_error_is_redacted(setup):
    client, _, factory = setup
    factory.side_effect = RuntimeError('https://secret:password@broker/?token='+TOKEN)
    response = client.post('/api/admin/options/AAPL/capture', headers={'Authorization':'Bearer '+TOKEN})
    assert response.status_code == 503
    assert 'password' not in response.text and TOKEN not in response.text


@pytest.mark.parametrize('iv', [float('nan'), float('inf'), -1, 0, True, None])
def test_invalid_volatility_is_not_persistable(iv):
    assert not capture.meaningful({'status':'OK','features':{'atm_iv':iv}})
