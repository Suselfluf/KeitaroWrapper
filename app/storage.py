from pathlib import Path

from sqlalchemy import Boolean, CheckConstraint, Integer, create_engine, false
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class StreamOfferMemory(Base):
    __tablename__ = "stream_offer_memory"
    __table_args__ = (
        CheckConstraint(
            "typeof(stream_id) = 'integer' AND stream_id > 0", name="ck_stream_offer_stream_id"
        ),
        CheckConstraint(
            "typeof(offer_id) = 'integer' AND offer_id > 0", name="ck_stream_offer_offer_id"
        ),
        CheckConstraint("pinned IN (0, 1)", name="ck_stream_offer_pinned"),
    )

    stream_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())


def create_storage_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    database = url.database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url)
