"""Semantic search wrapper over the Qdrant vector store."""

import structlog
from qdrant_client import QdrantClient, models

from app.core.config import settings
from app.schemas.generation import SearchResult
from app.services.ingestion import embed_texts, ensure_collection

logger = structlog.get_logger()


def semantic_search(
    qdrant: QdrantClient,
    query: str,
    top_k: int = 5,
    category_filter: str | None = None,
    source_id_filter: str | None = None,
) -> list[SearchResult]:
    """Embed *query* and return the *top_k* most similar chunks from Qdrant.

    Args:
        qdrant: Qdrant client instance.
        query: Natural-language search query.
        top_k: Maximum number of results to return.
        category_filter: Optional category to restrict results.
        source_id_filter: Optional source UUID to restrict results.

    Returns:
        Ranked list of ``SearchResult`` objects.
    """
    ensure_collection(qdrant)

    try:
        query_vector = embed_texts([query])[0]
    except RuntimeError:
        logger.error("Failed to embed search query")
        return []

    # Build optional Qdrant filter
    conditions: list[models.FieldCondition] = []
    if category_filter:
        conditions.append(
            models.FieldCondition(
                key="category",
                match=models.MatchValue(value=category_filter),
            )
        )
    if source_id_filter:
        conditions.append(
            models.FieldCondition(
                key="source_id",
                match=models.MatchValue(value=source_id_filter),
            )
        )

    query_filter = models.Filter(must=conditions) if conditions else None

    try:
        results = qdrant.search(
            collection_name=settings.qdrant_collection,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        )
    except Exception as exc:
        logger.error("Qdrant search failed", error=str(exc))
        return []

    search_results: list[SearchResult] = []
    for hit in results:
        payload = hit.payload or {}
        search_results.append(
            SearchResult(
                chunk_id=str(hit.id),
                source_id=payload.get("source_id", ""),
                source_title=payload.get("source_title", ""),
                text=payload.get("text", ""),
                score=hit.score,
                metadata={
                    k: v
                    for k, v in payload.items()
                    if k not in ("text", "content_hash")
                },
            )
        )

    logger.info("Semantic search completed", query=query[:80], results=len(search_results))
    return search_results


def get_chunks_for_source(
    qdrant: QdrantClient,
    source_id: str,
) -> list[dict]:
    """Retrieve all chunks belonging to a source, ordered by ``chunk_index``.

    Args:
        qdrant: Qdrant client instance.
        source_id: UUID of the source document.

    Returns:
        Sorted list of chunk dictionaries.
    """
    ensure_collection(qdrant)

    all_chunks: list[dict] = []
    offset = None
    try:
        while True:
            result = qdrant.scroll(
                collection_name=settings.qdrant_collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_id",
                            match=models.MatchValue(value=source_id),
                        )
                    ]
                ),
                limit=500,
                offset=offset,
                with_payload=True,
            )
            points, next_offset = result
            for point in points:
                payload = point.payload or {}
                all_chunks.append(
                    {
                        "chunk_id": str(point.id),
                        "chunk_index": payload.get("chunk_index", 0),
                        "text": payload.get("text", ""),
                        "word_count": payload.get("word_count", 0),
                        "char_start": payload.get("char_start", 0),
                    }
                )
            if next_offset is None:
                break
            offset = next_offset
    except Exception as exc:
        logger.error("Failed to scroll chunks for source", source_id=source_id, error=str(exc))

    all_chunks.sort(key=lambda c: c["chunk_index"])
    return all_chunks
