from __future__ import annotations

import logging
from functools import lru_cache

from sentence_transformers import SentenceTransformer

from app.config import get_settings

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    """Lazy-load the sentence-transformer model (singleton)."""
    global _model
    if _model is None:
        settings = get_settings()
        model_name = settings.sentence_transformer_model
        logger.info("Loading sentence-transformer model: %s", model_name)
        _model = SentenceTransformer(model_name)
        logger.info("Sentence-transformer model loaded successfully")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using sentence-transformers. Returns list of float vectors."""
    model = get_embedding_model()
    embeddings = model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    return embeddings.tolist()


def embed_single(text: str) -> list[float]:
    """Embed a single text string."""
    return embed_texts([text])[0]