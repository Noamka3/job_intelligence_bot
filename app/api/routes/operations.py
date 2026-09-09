from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import CrawlRunStatus
from app.schemas.operations import CrawlNowResult
from app.services.embeddings import get_embedding_provider
from app.services.embeddings.base import EmbeddingProvider
from app.services.jobs.ingestion import crawl_source, get_due_sources

router = APIRouter(prefix="/operations", tags=["operations"])
logger = logging.getLogger(__name__)


@router.post("/crawl-now", response_model=CrawlNowResult)
def crawl_now(
    db: Session = Depends(get_db),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> CrawlNowResult:
    """Crawls every due CareerSource synchronously, in-request.

    Runs sequentially and can take a while with many due sources - this is
    a manual/dev trigger. Phase 6 replaces the "every 5 minutes" part of
    this with a Celery Beat dispatcher that fans work out to a worker
    instead of blocking one HTTP request.
    """
    sources = get_due_sources(db)
    succeeded = sum(
        1
        for source in sources
        if crawl_source(db, source, embedding_provider).status == CrawlRunStatus.SUCCESS
    )
    return CrawlNowResult(
        sources_attempted=len(sources), succeeded=succeeded, failed=len(sources) - succeeded
    )
