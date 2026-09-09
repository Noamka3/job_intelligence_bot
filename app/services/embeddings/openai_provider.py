from __future__ import annotations

from openai import OpenAI

from app.core.config import get_settings


class OpenAIEmbeddingProvider:
    """Default EmbeddingProvider implementation, backed by the OpenAI API.

    Uses the `dimensions` parameter to shrink text-embedding-3-large's
    native 3072-dim output down to Settings.embedding_dimensions (1024 by
    default), matching the fixed vector(1024) columns in the schema - see
    app/models/constants.py.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.embedding_model
        self.dimensions = settings.embedding_dimensions

    def embed_one(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._client.embeddings.create(
            model=self._model,
            input=texts,
            dimensions=self.dimensions,
        )
        return [item.embedding for item in response.data]
