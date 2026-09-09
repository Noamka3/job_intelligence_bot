from functools import lru_cache

from app.core.config import get_settings
from app.services.embeddings.base import EmbeddingProvider
from app.services.embeddings.local_provider import LocalEmbeddingProvider
from app.services.embeddings.openai_provider import OpenAIEmbeddingProvider

__all__ = [
    "EmbeddingProvider",
    "LocalEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "get_embedding_provider",
]


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """The single place that decides which EmbeddingProvider implementation
    is active (Settings.embedding_provider). Everything else in the app
    depends on this function, never on a concrete provider class directly.

    Cached: LocalEmbeddingProvider loads an ONNX model into memory on
    construction, which should happen once per process, not once per call.
    """
    settings = get_settings()
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddingProvider()
    return LocalEmbeddingProvider()
