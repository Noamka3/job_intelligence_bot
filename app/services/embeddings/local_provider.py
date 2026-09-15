from __future__ import annotations

import logging

from fastembed import TextEmbedding
from onnxruntime.capi.onnxruntime_pybind11_state import RuntimeException as OnnxRuntimeException
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class LocalEmbeddingProvider:
    """Free, local EmbeddingProvider - runs entirely on this machine via
    fastembed (ONNX Runtime, no PyTorch, no API key, no network call).

    The default provider (spec's suggested OpenAI default was swapped for
    this one at the user's request to avoid any per-embedding cost - see
    docs/architecture.md). First construction downloads and caches the
    model (~240MB for the default multilingual MiniLM model) under
    $FASTEMBED_CACHE_PATH, or <system temp dir>/fastembed_cache when that
    isn't set (fastembed's own default - not ~/.cache); later runs reuse
    that cache.

    Observed in development: this machine's antivirus real-time scanning
    occasionally collides with ONNX Runtime's memory allocation for the
    model, surfacing as a transient "bad allocation" RuntimeException -
    not a real resource exhaustion (retrying immediately succeeds). Retried
    automatically here so a crawl never has to be re-run by hand for it.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._model = TextEmbedding(model_name=settings.local_embedding_model)
        self.dimensions = settings.embedding_dimensions

    def embed_one(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    @retry(
        retry=retry_if_exception_type(OnnxRuntimeException),
        stop=stop_after_attempt(3),
        wait=wait_fixed(2),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._model.embed(texts)]
