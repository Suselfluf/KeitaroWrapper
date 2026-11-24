"""Persist known stream offer associations and pins."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stream_offer_memory",
        sa.Column("stream_id", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("stream_id", "offer_id"),
        sa.CheckConstraint(
            "typeof(stream_id) = 'integer' AND stream_id > 0", name="ck_stream_offer_stream_id"
        ),
        sa.CheckConstraint(
            "typeof(offer_id) = 'integer' AND offer_id > 0", name="ck_stream_offer_offer_id"
        ),
        sa.CheckConstraint("pinned IN (0, 1)", name="ck_stream_offer_pinned"),
    )


def downgrade() -> None:
    op.drop_table("stream_offer_memory")
