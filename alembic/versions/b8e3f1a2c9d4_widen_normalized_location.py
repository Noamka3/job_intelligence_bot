"""widen normalized_location to match location_text

Revision ID: b8e3f1a2c9d4
Revises: 7a2d4c9e1f58
Create Date: 2026-09-18 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e3f1a2c9d4'
down_revision: Union[str, Sequence[str], None] = '7a2d4c9e1f58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Derived from location_text (500), so it can never need less: a
    # nine-office Workday posting overflowed the old 255 and failed the
    # whole crawl.
    op.alter_column(
        'job_postings', 'normalized_location',
        existing_type=sa.String(length=255), type_=sa.String(length=500),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'job_postings', 'normalized_location',
        existing_type=sa.String(length=500), type_=sa.String(length=255),
    )
