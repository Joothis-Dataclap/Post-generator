"""Semantic search API endpoint."""

from fastapi import APIRouter

from app.core.dependencies import QdrantDep
from app.schemas.generation import SearchRequest, SearchResponse
from app.services.retrieval import semantic_search

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search_chunks(body: SearchRequest, qdrant: QdrantDep) -> SearchResponse:
    """Semantic search over all stored knowledge-base chunks.

    Embeds the query, searches Qdrant for the ``top_k`` most similar chunks,
    and optionally filters by ``category_filter``.
    """
    results = semantic_search(
        qdrant,
        query=body.query,
        top_k=body.top_k,
        category_filter=body.category_filter,
    )
    return SearchResponse(query=body.query, results=results)
