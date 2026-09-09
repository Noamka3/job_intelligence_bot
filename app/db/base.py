"""Declarative base shared by every ORM model.

Alembic's env.py imports app.models (which import this Base) so autogenerate
can see the full schema.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
