"""The two places a URL from the outside world becomes dangerous: one the
crawler fetches, and one the dashboard renders as a link."""

from __future__ import annotations

import pytest

from app.ingestion.adapters._http import BlockedUrlError, ensure_public_url
from app.ingestion.adapters.base import JobDetails, JobStub


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/dashboard/stats",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/admin",
        "http://192.168.1.1/",
        "http://172.16.0.9:5544/",
        "http://[::1]:8000/",
        "http://0.0.0.0:6380/",
    ],
)
def test_private_addresses_are_refused(url: str) -> None:
    with pytest.raises(BlockedUrlError):
        ensure_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://boards-api.greenhouse.io/v1/boards/torq/jobs",
        "https://www.comeet.com/jobs/acme/F1.008",
        "http://93.184.216.34/careers",  # a public literal address is fine
        "https://careers.example.co.il/jobs?x=1",
    ],
)
def test_public_urls_pass(url: str) -> None:
    ensure_public_url(url)  # does not raise


def test_a_hostname_is_left_to_dns() -> None:
    """Names are not resolved here: every URL this project crawls comes
    from the owner's own spreadsheet, and resolving would mean a DNS
    round-trip per call - the guard is against the literal addresses that
    make a crawler useful to an attacker."""
    ensure_public_url("https://internal.example/")  # does not raise


@pytest.mark.parametrize("scheme", ["javascript", "data", "vbscript", "file"])
def test_dangerous_apply_urls_are_dropped_at_ingest(scheme: str) -> None:
    """A career page controls what it puts in a JSON-LD "url" field, and
    the dashboard renders apply_url as a real link."""
    payload = {
        "external_job_id": "1",
        "title": "Junior Engineer",
        "source_url": "https://careers.example.com/jobs/1",
        "apply_url": f"{scheme}:alert(document.cookie)",
    }

    assert JobStub(**payload).apply_url is None
    assert JobDetails(**payload).apply_url is None


def test_ordinary_apply_urls_survive() -> None:
    stub = JobStub(
        external_job_id="1",
        title="Junior Engineer",
        source_url="https://careers.example.com/jobs/1",
        apply_url="https://careers.example.com/jobs/1/apply",
    )
    assert stub.apply_url == "https://careers.example.com/jobs/1/apply"

    # A relative path is not a scheme and is left alone; the dashboard
    # decides whether it can be linked.
    relative = JobStub(
        external_job_id="1",
        title="Junior Engineer",
        source_url="https://careers.example.com/jobs/1",
        apply_url="/jobs/1/apply",
    )
    assert relative.apply_url == "/jobs/1/apply"
