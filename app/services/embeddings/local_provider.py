from __future__ import annotations

import logging
import math

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
        """One vector for a text of any length. The model reads at most
        ~512 tokens and silently truncates (fastembed enables truncation
        unconditionally), which for a multi-page CV meant only the first
        page counted and for a long posting the requirements at the end
        were never seen. Longer texts are embedded in chunks and the
        chunk vectors are averaged and re-normalized - the standard
        long-document approach for sentence-transformer models."""
        chunks = _chunk_words(text, _CHUNK_WORDS)
        if len(chunks) <= 1:
            return self.embed_many([text])[0]
        vectors = self.embed_many(chunks)
        mean = [sum(column) / len(vectors) for column in zip(*vectors, strict=True)]
        norm = math.sqrt(sum(value * value for value in mean))
        return [value / norm for value in mean] if norm else mean

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


# ~220 words is comfortably inside the 512-token window for mixed
# Hebrew/English text (Hebrew tokenizes to more pieces per word).
_CHUNK_WORDS = 220


def _chunk_words(text: str, size: int) -> list[str]:
    words = text.split()
    if len(words) <= size:
        return [text]
    return [" ".join(words[start : start + size]) for start in range(0, len(words), size)]
