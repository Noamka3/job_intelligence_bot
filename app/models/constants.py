"""Constants shared by model definitions.

EMBEDDING_DIM must match Settings.embedding_dimensions. It is fixed here
(rather than read from Settings at import time) because it defines actual
column DDL: changing it means writing a new Alembic migration that
recreates the vector columns, not just editing an env var.
"""

EMBEDDING_DIM = 1024
