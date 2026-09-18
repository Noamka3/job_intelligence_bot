"""add job details_fetched_at

Revision ID: 7a2d4c9e1f58
Revises: f690f5712b6d
Create Date: 2026-09-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a2d4c9e1f58'
down_revision: Union[str, Sequence[str], None] = 'f690f5712b6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Left NULL for existing rows on purpose: each is fetched once more on
    # its next crawl, which stamps it.
    op.add_column(
        'job_postings', sa.Column('details_fetched_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_postings', 'details_fetched_at')
