import uuid

import pytest

from app.api import ice
from app.core.config import settings


@pytest.mark.asyncio
async def test_stun_only_when_turn_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "metered_app_name", "")
    monkeypatch.setattr(settings, "metered_api_key", "")
    res = await ice.get_ice_servers(uuid.uuid4())
    assert res["turn"] is False and res["ice_servers"] == ice.STUN_ONLY


@pytest.mark.asyncio
async def test_falls_back_to_stun_when_provider_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "metered_app_name", "nonexistent-app-for-test")
    monkeypatch.setattr(settings, "metered_api_key", "x")

    class Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): raise RuntimeError("offline")
        async def __aexit__(self, *a): ...

    monkeypatch.setattr(ice.httpx, "AsyncClient", Boom)
    res = await ice.get_ice_servers(uuid.uuid4())
    assert res["turn"] is False


@pytest.mark.asyncio
async def test_mints_credentials_with_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "metered_app_name", "demo")
    monkeypatch.setattr(settings, "metered_api_key", "secret")
    seen = {}

    class Resp:
        def raise_for_status(self): ...
        def json(self): return {"username": "u", "password": "p"}

    class Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def post(self, url, params=None, json=None):
            seen.update(url=url, params=params)
            return Resp()

    monkeypatch.setattr(ice.httpx, "AsyncClient", Client)
    res = await ice.get_ice_servers(uuid.uuid4())
    assert res["turn"] is True
    assert seen["url"] == "https://demo.metered.live/api/v1/turn/credential"
    assert seen["params"] == {"secretKey": "secret"}
    relay = res["ice_servers"][-1]
    assert relay["username"] == "u" and relay["credential"] == "p"
    assert any(u.startswith("turn:global.relay.metered.ca") for u in relay["urls"])
