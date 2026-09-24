"""drop never-written columns

Revision ID: e1f2a3b4c5d6
Revises: c4d5e6f7a8b9
Create Date: 2026-09-24 19:05:00.000000

job_postings.experience_max_years and companies.canonical_career_url
were in the initial schema and nothing ever wrote them: seniority reads
only the experience floor, and the resolver stores what it finds on the
CareerSource row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('job_postings', 'experience_max_years')
    op.drop_column('companies', 'canonical_career_url')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('companies', sa.Column('canonical_career_url', sa.String(length=2048), nullable=True))
    op.add_column('job_postings', sa.Column('experience_max_years', sa.Integer(), nullable=True))
