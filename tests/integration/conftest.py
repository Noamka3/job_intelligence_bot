"""Integration-test-only fixtures (scoped here, not in the root conftest,
so pure unit tests never need a DB connection just for this).
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.candidate_profile import CandidateProfile
from app.models.target_role import TargetRole


@pytest.fixture(autouse=True)
def _isolate_from_real_dev_data(db_session: Session) -> None:
    """The dev DB accumulates real data from manual testing (real
    candidate profiles, target roles, companies, jobs). Tests that assume
    a pristine table ("no active profile exists yet", "only these two
    target roles exist", "exactly N profiles exist") would otherwise
    flake depending on what was manually tested most recently.

    Clearing candidate_profiles and target_roles here - at the start of
    every integration test, inside that test's own transaction (see
    db_session in the root conftest) - keeps tests deterministic without
    ever touching real committed data: everything here rolls back when
    the test ends. Company/job-table tests are isolated by scoping their
    own queries/assertions instead (see e.g. test_company_sync.py), since
    those tables are too large to usefully wipe per test.
    """
    db_session.execute(delete(CandidateProfile))
    db_session.execute(delete(TargetRole))
    db_session.flush()
