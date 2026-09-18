"""WordPress career pages whose job list is drawn by JavaScript but whose
jobs are ordinary posts of a custom type the site's REST API exposes
(Comblack `careers`, One `job`, OMC `career`, Tap `awsm_job_openings` -
4 of the 28 WordPress sites in the sheet; the others keep their jobs in a
plugin the API doesn't show). The resolver finds the type through
/wp-json/wp/v2/types and stores its REST base as the source's
external_identifier.

GET {origin}/wp-json/wp/v2/{rest_base}?per_page=100&page=N&_fields=...
    -> [{id, link, date_gmt, modified_gmt, title: {rendered}, content: {rendered}}]

When the API returns no text for a post (One renders its jobs from
page-builder fields it doesn't expose), the post's own page is read
instead, the way a plain career page's job page is. Jobs are identified
by their page URL exactly as the generic reader identifies them, so a
site that was crawled generically before keeps its stored jobs.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.ingestion.adapters._http import get_json
from app.ingestion.adapters._util import parse_timestamp
from app.ingestion.adapters.base import JobDetails, JobStub
from app.ingestion.adapters.generic_html import GenericHtmlAdapter, job_id_for_url
from app.models.career_source import CareerSource
from app.services.jobs.html_text import html_to_text

_PAGE_SIZE = 100
_MAX_PAGES = 10
_FIELDS = "id,link,date_gmt,modified_gmt,title,content"


class WordPressAdapter:
    def __init__(self) -> None:
        self._page_reader = GenericHtmlAdapter()

    def list_jobs(self, source: CareerSource) -> list[JobStub]:
        stubs: list[JobStub] = []
        for page in range(1, _MAX_PAGES + 1):
            posts = get_json(
                _posts_url(source),
                params={"per_page": _PAGE_SIZE, "page": page, "_fields": _FIELDS},
            )
            stubs.extend(
                self._to_stub(post) for post in posts if post.get("id") and post.get("link")
            )
            if len(posts) < _PAGE_SIZE:
                return stubs
        return stubs

    def fetch_job(self, source: CareerSource, stub: JobStub) -> JobDetails:
        description = html_to_text(_rendered(stub.raw or {}, "content"))
        if not description:
            return self._page_reader.fetch_job(source, stub)
        return JobDetails(
            external_job_id=stub.external_job_id,
            title=stub.title,
            description=description,
            source_url=stub.source_url,
            apply_url=stub.apply_url,
            source_published_at=parse_timestamp((stub.raw or {}).get("date_gmt")),
            source_updated_at=stub.source_updated_at,
        )

    @staticmethod
    def _to_stub(post: dict[str, Any]) -> JobStub:
        link = str(post["link"])
        return JobStub(
            external_job_id=job_id_for_url(link),
            title=html_to_text(_rendered(post, "title")),
            source_url=link,
            apply_url=link,
            source_updated_at=parse_timestamp(post.get("modified_gmt")),
            raw=post,
        )


def _posts_url(source: CareerSource) -> str:
    parsed = urlparse(source.source_url)
    return f"{parsed.scheme}://{parsed.netloc}/wp-json/wp/v2/{source.external_identifier}"


def _rendered(post: dict[str, Any], field: str) -> str | None:
    value = post.get(field)
    return value.get("rendered") if isinstance(value, dict) else value
