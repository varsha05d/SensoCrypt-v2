"""The failure_reason column belongs on Call ONLY. It once landed on Session by mistake, which
broke every query on the live `sessions` table (it has no such column) and so every
verification handshake -- while all the (database-mocking) tests still passed."""

from app.db.models import Base, Call


def test_failure_reason_is_on_call_and_nowhere_else():
    owners = [t.name for t in Base.metadata.tables.values() if "failure_reason" in t.columns]
    assert owners == [Call.__tablename__]


def test_startup_migration_adds_exactly_the_new_call_column():
    import inspect

    from app import main

    src = inspect.getsource(main.lifespan)
    assert "ALTER TABLE calls ADD COLUMN IF NOT EXISTS failure_reason" in src
