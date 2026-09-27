"""add jev columns

Revision ID: f7a8b9c0d1e2
Revises: e1f2a3b4c5d6
Create Date: 2026-09-27 15:40:00.000000

Where Jev's reads live: job_postings.jev_reading (what the posting is)
and job_matches.jev_fit (would a recruiter shortlist the CV for it).
Both nullable JSONB: empty until Jev is on and has read the row, and
they carry the content hash the answers were given for.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('job_postings', sa.Column('jev_reading', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('job_matches', sa.Column('jev_fit', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('job_matches', 'jev_fit')
    op.drop_column('job_postings', 'jev_reading')
