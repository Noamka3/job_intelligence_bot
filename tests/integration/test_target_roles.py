from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.schemas.target_role import TargetRoleCreate, TargetRoleUpdate
from app.services import target_roles
from tests.conftest import FakeEmbeddingProvider


def test_create_target_role_generates_embedding(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    data = TargetRoleCreate(
        canonical_name="Junior Software Engineer",
        aliases=["Software Engineer I"],
        positive_keywords=["python"],
    )

    role = target_roles.create_target_role(db_session, data, fake_embedding_provider)

    assert role.id is not None
    assert role.embedding is not None
    assert fake_embedding_provider.calls


def test_update_target_role_reembeds_only_when_relevant_field_changes(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    role = target_roles.create_target_role(
        db_session,
        TargetRoleCreate(canonical_name="Junior Software Engineer"),
        fake_embedding_provider,
    )
    calls_after_create = len(fake_embedding_provider.calls)

    target_roles.update_target_role(
        db_session, role.id, TargetRoleUpdate(enabled=False), fake_embedding_provider
    )
    assert len(fake_embedding_provider.calls) == calls_after_create

    target_roles.update_target_role(
        db_session,
        role.id,
        TargetRoleUpdate(description="Entry-level backend role"),
        fake_embedding_provider,
    )
    assert len(fake_embedding_provider.calls) == calls_after_create + 1


def test_update_target_role_raises_for_missing_id(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    with pytest.raises(target_roles.TargetRoleNotFoundError):
        target_roles.update_target_role(
            db_session, 999_999, TargetRoleUpdate(enabled=False), fake_embedding_provider
        )


def test_list_target_roles_orders_by_name(
    db_session: Session, fake_embedding_provider: FakeEmbeddingProvider
) -> None:
    target_roles.create_target_role(
        db_session, TargetRoleCreate(canonical_name="Zebra Role"), fake_embedding_provider
    )
    target_roles.create_target_role(
        db_session, TargetRoleCreate(canonical_name="Alpha Role"), fake_embedding_provider
    )

    roles = target_roles.list_target_roles(db_session)

    assert [r.canonical_name for r in roles] == ["Alpha Role", "Zebra Role"]
