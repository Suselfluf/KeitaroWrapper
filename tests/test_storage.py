from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.storage import StreamOfferMemory, create_storage_engine


@pytest.fixture
def migrated_database(monkeypatch, tmp_path):
    url = f"sqlite:///{tmp_path / 'nested' / 'storage.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_storage_engine(url)
    try:
        yield config, engine
    finally:
        engine.dispose()


def test_migration_has_exact_approved_columns_and_downgrades(migrated_database):
    config, engine = migrated_database
    schema = inspect(engine)
    columns = schema.get_columns("stream_offer_memory")
    assert [column["name"] for column in columns] == ["stream_id", "offer_id", "pinned"]
    assert all(not column["nullable"] for column in columns)
    assert [str(column["type"]) for column in columns] == ["INTEGER", "INTEGER", "BOOLEAN"]
    assert schema.get_pk_constraint("stream_offer_memory")["constrained_columns"] == [
        "stream_id",
        "offer_id",
    ]
    assert schema.get_foreign_keys("stream_offer_memory") == []
    command.check(config)

    command.downgrade(config, "base")
    assert "stream_offer_memory" not in inspect(engine).get_table_names()


def test_saves_and_reads_independent_associations_and_pin(migrated_database):
    _, engine = migrated_database
    with Session(engine) as session, session.begin():
        session.add_all(
            [
                StreamOfferMemory(stream_id=1, offer_id=2),
                StreamOfferMemory(stream_id=3, offer_id=2),
            ]
        )
    with Session(engine) as session, session.begin():
        first = session.get(StreamOfferMemory, (1, 2))
        assert first.pinned is False
        first.pinned = True
    with Session(engine) as session:
        assert session.get(StreamOfferMemory, (1, 2)).pinned is True
        assert session.get(StreamOfferMemory, (3, 2)).pinned is False


@pytest.mark.parametrize(
    "values",
    [(0, 2, 0), (1, -2, 0), (1.5, 2, 0), (1, "float", 0), (1, 2, 9)],
    ids=["zero-stream-id", "negative-offer-id", "fractional-stream-id", "text-offer-id", "pin"],
)
def test_storage_rejects_invalid_values(migrated_database, values):
    _, engine = migrated_database
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.exec_driver_sql("INSERT INTO stream_offer_memory VALUES (?, ?, ?)", values)


def test_rejects_duplicate_association(migrated_database):
    _, engine = migrated_database
    with engine.begin() as connection:
        connection.exec_driver_sql("INSERT INTO stream_offer_memory VALUES (1, 2, 0)")
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.exec_driver_sql("INSERT INTO stream_offer_memory VALUES (1, 2, 1)")
