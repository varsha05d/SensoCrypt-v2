import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.api import ice
from app.core.config import settings

NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


class _Store:
    """In-memory stand-in for the turn_credentials row + the DB session that serves it."""

    def __init__(self):
        self.row = None


def _install(monkeypatch, store, mint_results):
    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, model, pk): return store.row
        def add(self, row): store.row = row
        async def commit(self): ...

    calls = {"mint": 0}

    async def fake_mint():
        calls["mint"] += 1
        r = mint_results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ice, "async_session", lambda: Session())
    monkeypatch.setattr(ice, "_mint", fake_mint)
    monkeypatch.setattr(settings, "metered_app_name", "demo")
    monkeypatch.setattr(settings, "metered_api_key", "secret")
    return calls


@pytest.mark.asyncio
async def test_stun_only_when_turn_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "metered_app_name", "")
    monkeypatch.setattr(settings, "metered_api_key", "")
    res = await ice.get_ice_servers(uuid.uuid4())
    assert res["turn"] is False and res["ice_servers"] == ice.STUN_ONLY


@pytest.mark.asyncio
async def test_first_ever_credential_is_minted_and_served(monkeypatch):
    store = _Store()
    calls = _install(monkeypatch, store, [{"username": "u1", "password": "p1"}])
    res = await ice.get_ice_servers(uuid.uuid4())
    assert res["turn"] is True
    relay = res["ice_servers"][-1]
    assert relay["username"] == "u1" and relay["credential"] == "p1"
    assert any(u.startswith("turn:global.relay.metered.ca") for u in relay["urls"])
    assert calls["mint"] == 1 and store.row.username == "u1"


@pytest.mark.asyncio
async def test_stored_credential_is_reused_across_restarts_without_minting(monkeypatch):
    store = _Store()
    calls = _install(monkeypatch, store, [{"username": "u1", "password": "p1"}])
    await ice.ensure_credential()                       # "first boot"
    again = await ice.ensure_credential()               # "after a restart/redeploy": row persisted in the DB
    assert again == ("u1", "p1") and calls["mint"] == 1


@pytest.mark.asyncio
async def test_replacement_is_minted_a_day_early_and_not_served_until_mature(monkeypatch):
    store = _Store()
    calls = _install(monkeypatch, store, [{"username": "u1", "password": "p1"}, {"username": "u2", "password": "p2"}])
    await ice.ensure_credential()
    store.row.expires_at = NOW() + timedelta(hours=10)  # inside the renewal window
    cred = await ice.ensure_credential()
    assert cred == ("u1", "p1"), "keep serving the proven credential"
    assert store.row.next_username == "u2" and calls["mint"] == 2


@pytest.mark.asyncio
async def test_matured_replacement_is_promoted_when_current_is_nearly_gone(monkeypatch):
    store = _Store()
    _install(monkeypatch, store, [{"username": "u1", "password": "p1"}])
    await ice.ensure_credential()
    store.row.expires_at = NOW() + timedelta(seconds=100)
    store.row.next_username, store.row.next_password = "u2", "p2"
    store.row.next_expires_at = NOW() + timedelta(days=30)
    store.row.next_created_at = NOW() - timedelta(minutes=10)   # long since propagated
    assert await ice.ensure_credential() == ("u2", "p2")


@pytest.mark.asyncio
async def test_immature_replacement_is_not_promoted(monkeypatch):
    store = _Store()
    _install(monkeypatch, store, [{"username": "u1", "password": "p1"}])
    await ice.ensure_credential()
    store.row.expires_at = NOW() + timedelta(seconds=100)
    store.row.next_username, store.row.next_password = "u2", "p2"
    store.row.next_expires_at = NOW() + timedelta(days=30)
    store.row.next_created_at = NOW() - timedelta(seconds=20)   # minted seconds ago: not yet accepted
    cred = await ice.ensure_credential()
    assert cred == ("u1", "p1")


@pytest.mark.asyncio
async def test_falls_back_to_stun_when_the_provider_is_unreachable(monkeypatch):
    store = _Store()
    _install(monkeypatch, store, [RuntimeError("offline")])
    res = await ice.get_ice_servers(uuid.uuid4())
    assert res["turn"] is False and res["ice_servers"] == ice.STUN_ONLY


@pytest.mark.asyncio
async def test_switching_metered_account_does_not_reuse_the_old_accounts_credential(monkeypatch):
    rows = {}

    class Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, model, pk): return rows.get(pk)
        def add(self, row): rows[row.id] = row
        async def commit(self): ...

    minted = [{"username": "old-user", "password": "old-pw"}, {"username": "new-user", "password": "new-pw"}]

    async def fake_mint():
        return minted.pop(0)

    monkeypatch.setattr(ice, "async_session", lambda: Session())
    monkeypatch.setattr(ice, "_mint", fake_mint)
    monkeypatch.setattr(settings, "metered_api_key", "secret")
    monkeypatch.setattr(settings, "metered_app_name", "old-account")
    assert await ice.ensure_credential() == ("old-user", "old-pw")
    assert await ice.ensure_credential() == ("old-user", "old-pw")       # same account: reused
    monkeypatch.setattr(settings, "metered_app_name", "new-account")      # the user switches accounts
    assert await ice.ensure_credential() == ("new-user", "new-pw")       # fresh login for the new account
