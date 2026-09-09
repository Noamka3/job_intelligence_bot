from app.services.embeddings.base import EmbeddingProvider
from app.services.embeddings.openai_provider import OpenAIEmbeddingProvider

__all__ = ["EmbeddingProvider", "OpenAIEmbeddingProvider", "get_embedding_provider"]


def get_embedding_provider() -> EmbeddingProvider:
    """The single place that decides which EmbeddingProvider implementation
    is active. Everything else in the app depends on this function, never
    on OpenAIEmbeddingProvider directly, so swapping providers later means
    changing one line here.
    """
    return OpenAIEmbeddingProvider()
