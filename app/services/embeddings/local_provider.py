from __future__ import annotations

from fastembed import TextEmbedding

from app.core.config import get_settings


class LocalEmbeddingProvider:
    """Free, local EmbeddingProvider - runs entirely on this machine via
    fastembed (ONNX Runtime, no PyTorch, no API key, no network call).

    The default provider (spec's suggested OpenAI default was swapped for
    this one at the user's request to avoid any per-embedding cost - see
    docs/architecture.md). First construction downloads and caches the
    model (~220MB for the default multilingual MiniLM model) under
    ~/.cache/fastembed/; later runs reuse that cache.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._model = TextEmbedding(model_name=settings.local_embedding_model)
        self.dimensions = settings.embedding_dimensions

    def embed_one(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._model.embed(texts)]
