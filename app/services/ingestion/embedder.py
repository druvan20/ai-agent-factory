from __future__ import annotations

import logging
from typing import Any

from openai import OpenAI  # type: ignore[import-untyped]

from app.config import get_settings
from app.services.ingestion.chunker import Chunk
from app.store.vector_store import get_documents_collection

logger = logging.getLogger(__name__)

BATCH_SIZE = 96


def embed_and_store(chunks: list[Chunk], project_id: str) -> int:
    """Embed chunks via OpenAI and upsert into ChromaDB. Returns count stored."""
    if not chunks:
        return 0

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    collection = get_documents_collection(project_id)

    total_stored = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        texts = [c.content for c in batch]

        response = client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
        )
        vectors: list[list[float]] = [item.embedding for item in response.data]

        collection.upsert(
            ids=[c.chunk_id for c in batch],
            documents=texts,
            embeddings=vectors,  # type: ignore[arg-type]
            metadatas=[c.metadata for c in batch],  # type: ignore[arg-type]
        )
        total_stored += len(batch)

    logger.info("Embedded %d chunks for project %s", total_stored, project_id)
    return total_stored