"""switch to 384-dim local embeddings

Revision ID: da3cb6356625
Revises: 649209bb46a8
Create Date: 2026-09-09 19:16:49.197310

Switches the default EmbeddingProvider from OpenAI (1024 dims) to a free,
local ONNX model (384 dims) - see app/services/embeddings/local_provider.py.
No production data exists in these columns yet (embedding generation was
never completed against a real OpenAI key), so this drops and recreates
each column rather than attempting an in-place ALTER COLUMN TYPE, which
would otherwise need to reconcile incompatible vector dimensions.
"""
from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'da3cb6356625'
down_revision: Union[str, Sequence[str], None] = '649209bb46a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_job_postings_embedding_hnsw', table_name='job_postings')

    for table in ('candidate_profiles', 'target_roles', 'job_postings'):
        op.drop_column(table, 'embedding')
        op.add_column(
            table, sa.Column('embedding', pgvector.sqlalchemy.Vector(384), nullable=True)
        )

    op.create_index(
        'ix_job_postings_embedding_hnsw',
        'job_postings',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_with={'m': 16, 'ef_construction': 64},
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )


def downgrade() -> None:
    op.drop_index('ix_job_postings_embedding_hnsw', table_name='job_postings')

    for table in ('job_postings', 'target_roles', 'candidate_profiles'):
        op.drop_column(table, 'embedding')
        op.add_column(
            table, sa.Column('embedding', pgvector.sqlalchemy.Vector(1024), nullable=True)
        )

    op.create_index(
        'ix_job_postings_embedding_hnsw',
        'job_postings',
        ['embedding'],
        unique=False,
        postgresql_using='hnsw',
        postgresql_with={'m': 16, 'ef_construction': 64},
        postgresql_ops={'embedding': 'vector_cosine_ops'},
    )
