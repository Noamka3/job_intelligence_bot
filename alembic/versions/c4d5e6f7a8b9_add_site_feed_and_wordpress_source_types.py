"""add site_feed and wordpress source types

Revision ID: c4d5e6f7a8b9
Revises: b8e3f1a2c9d4
Create Date: 2026-09-18 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b8e3f1a2c9d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE career_source_type ADD VALUE IF NOT EXISTS 'SITE_FEED'")
    op.execute("ALTER TYPE career_source_type ADD VALUE IF NOT EXISTS 'WORDPRESS'")


def downgrade() -> None:
    """Downgrade schema."""
    # Postgres cannot remove a value from an enum type; unused values are
    # harmless, so the downgrade leaves them in place.
