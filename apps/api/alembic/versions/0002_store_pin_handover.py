"""store pin handover

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 17:26:48.535259
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # additive only (judges use the live service): a nullable column, and a NOT NULL column with a
    # server default so the proofs recorded before this revision stay valid
    op.add_column("app_user", sa.Column("delivery_pin", sa.String(length=6), nullable=True))
    op.add_column("proof", sa.Column("pin_verified", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("proof", "pin_verified")
    op.drop_column("app_user", "delivery_pin")
