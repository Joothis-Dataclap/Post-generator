"""RAG ingestion pipeline — text extraction, chunking, embedding, and Qdrant upsert.

Supported file types: PDF, DOCX, TXT, Markdown, HTML.
Deduplication via SHA-256 content hashing.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from io import BytesIO

import structlog
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.source import Source

logger = structlog.get_logger()

# ── Text splitter (respects sentence / paragraph boundaries) ─
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=150,
    separators=["\n\n", "\n", ". ", "? ", "! ", " ", ""],
    keep_separator=True,
)

# ── Lazy-loaded embedding model ─────────────────────────────
_local_model = None


def _get_local_model():
    """Lazy-load the sentence-transformers model on first use."""
    global _local_model
    if _local_model is None:
        from sentence_transformers import SentenceTransformer

        _local_model = SentenceTransformer(settings.embedding_model_local)
    return _local_model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using the configured provider.

    Returns a list of float vectors, one per input text.

    Raises:
        RuntimeError: If the embedding API call fails.
    """
    if settings.embedding_provider == "local":
        try:
            model = _get_local_model()
            embeddings = model.encode(texts, show_progress_bar=False)
            return [e.tolist() for e in embeddings]
        except Exception as exc:
            logger.error("Local embedding failed", error=str(exc))
            raise RuntimeError(f"Local embedding error: {exc}") from exc
    else:
        try:
            import openai

            client = openai.OpenAI(api_key=settings.openai_api_key)
            response = client.embeddings.create(
                input=texts,
                model=settings.embedding_model_openai,
            )
            return [d.embedding for d in response.data]
        except Exception as exc:
            logger.error("OpenAI embedding failed", error=str(exc))
            raise RuntimeError(f"OpenAI embedding error: {exc}") from exc


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# File extraction
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract plain text from a PDF byte stream."""
    from PyPDF2 import PdfReader

    reader = PdfReader(BytesIO(file_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages)


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract plain text from a DOCX byte stream."""
    from docx import Document

    doc = Document(BytesIO(file_bytes))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def extract_text_from_markdown(text: str) -> str:
    """Convert Markdown to plain text."""
    import markdown as md
    from bs4 import BeautifulSoup

    html = md.markdown(text)
    return BeautifulSoup(html, "html.parser").get_text(separator="\n")


def extract_text_from_html(text: str) -> str:
    """Strip HTML tags and return plain text."""
    from bs4 import BeautifulSoup

    return BeautifulSoup(text, "html.parser").get_text(separator="\n")


def extract_text(filename: str, content: bytes) -> str:
    """Dispatch to the correct extractor based on file extension.

    Falls back to UTF-8 decoding for unknown extensions.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        return extract_text_from_pdf(content)
    if ext == "docx":
        return extract_text_from_docx(content)
    if ext == "md":
        return extract_text_from_markdown(content.decode("utf-8", errors="replace"))
    if ext in ("html", "htm"):
        return extract_text_from_html(content.decode("utf-8", errors="replace"))
    return content.decode("utf-8", errors="replace")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Deduplication
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _hash_chunk(text: str) -> str:
    """Return the SHA-256 hex digest of a chunk's text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _get_existing_hashes(qdrant: QdrantClient) -> set[str]:
    """Retrieve all existing chunk content hashes from Qdrant for deduplication."""
    existing: set[str] = set()
    offset = None
    try:
        while True:
            result = qdrant.scroll(
                collection_name=settings.qdrant_collection,
                limit=500,
                offset=offset,
                with_payload=["content_hash"],
            )
            points, next_offset = result
            for point in points:
                h = (point.payload or {}).get("content_hash")
                if h:
                    existing.add(h)
            if next_offset is None:
                break
            offset = next_offset
    except Exception as exc:
        logger.warning("Could not fetch existing hashes (collection may be empty)", error=str(exc))
    return existing


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Collection bootstrap
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def ensure_collection(qdrant: QdrantClient) -> None:
    """Create the Qdrant collection if it does not already exist."""
    try:
        collections = [c.name for c in qdrant.get_collections().collections]
    except Exception as exc:
        logger.error("Failed to list Qdrant collections", error=str(exc))
        raise RuntimeError(f"Qdrant connection error: {exc}") from exc

    if settings.qdrant_collection not in collections:
        dim = settings.effective_embedding_dimension
        qdrant.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=models.VectorParams(
                size=dim,
                distance=models.Distance.COSINE,
            ),
        )
        logger.info("Created Qdrant collection", name=settings.qdrant_collection, dim=dim)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Main ingest function
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def ingest_source(
    *,
    db: AsyncSession,
    qdrant: QdrantClient,
    title: str,
    source_type: str,
    category: str,
    filename: str | None = None,
    file_bytes: bytes | None = None,
    text_content: str | None = None,
) -> Source:
    """Ingest a document end-to-end.

    1. Extract raw text (from file bytes or direct text).
    2. Chunk with recursive character splitter.
    3. Deduplicate via SHA-256 content hashing.
    4. Embed new chunks and upsert to Qdrant.
    5. Persist the ``Source`` record in SQLite.

    Args:
        db: Async SQLAlchemy session.
        qdrant: Qdrant client instance.
        title: Human-readable title for this source.
        source_type: One of article | doc | blog | product.
        category: Free-form category label.
        filename: Original upload filename (used for format detection).
        file_bytes: Raw file bytes (mutually exclusive with *text_content*).
        text_content: Plain text to ingest directly.

    Returns:
        The persisted ``Source`` ORM object.

    Raises:
        ValueError: If neither *text_content* nor *file_bytes* is provided.
    """
    ensure_collection(qdrant)

    # ── 1. Extract text ──────────────────────────────────────
    if text_content:
        raw_text = text_content
    elif file_bytes and filename:
        raw_text = extract_text(filename, file_bytes)
    else:
        raise ValueError("Provide either text_content or file upload")

    # ── 2. Build Source record ───────────────────────────────
    source_id = str(uuid.uuid4())
    source = Source(
        id=source_id,
        title=title,
        source_type=source_type,
        category=category,
        filename=filename,
        raw_text=raw_text,
    )

    # ── 3. Chunk ─────────────────────────────────────────────
    chunks = _splitter.split_text(raw_text)
    if not chunks:
        source.chunk_count = 0
        db.add(source)
        await db.commit()
        await db.refresh(source)
        return source

    # ── 4. Dedup ─────────────────────────────────────────────
    existing_hashes = _get_existing_hashes(qdrant)
    deduped: list[tuple[int, str, str]] = []
    char_offset = 0
    for idx, chunk_text in enumerate(chunks):
        h = _hash_chunk(chunk_text)
        if h not in existing_hashes:
            deduped.append((idx, chunk_text, h))
        char_offset += len(chunk_text)

    if not deduped:
        source.chunk_count = len(chunks)
        db.add(source)
        await db.commit()
        await db.refresh(source)
        logger.info("All chunks deduplicated", source_id=source_id)
        return source

    # ── 5. Embed ─────────────────────────────────────────────
    texts_to_embed = [t for _, t, _ in deduped]
    vectors = embed_texts(texts_to_embed)

    # ── 6. Build Qdrant points ───────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()
    points: list[models.PointStruct] = []
    running_offset = 0
    for (idx, chunk_text, content_hash), vector in zip(deduped, vectors):
        point_id = str(uuid.uuid4())
        points.append(
            models.PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "source_id": source_id,
                    "source_title": title,
                    "source_type": source_type,
                    "category": category,
                    "chunk_index": idx,
                    "char_start": running_offset,
                    "word_count": len(chunk_text.split()),
                    "created_at": now_iso,
                    "text": chunk_text,
                    "content_hash": content_hash,
                },
            )
        )
        running_offset += len(chunk_text)

    # ── 7. Upsert to Qdrant (batched) ───────────────────────
    batch_size = 100
    for i in range(0, len(points), batch_size):
        try:
            qdrant.upsert(
                collection_name=settings.qdrant_collection,
                points=points[i : i + batch_size],
            )
        except Exception as exc:
            logger.error("Qdrant upsert failed", batch_start=i, error=str(exc))
            raise RuntimeError(f"Qdrant upsert error: {exc}") from exc

    # ── 8. Persist source record ─────────────────────────────
    source.chunk_count = len(chunks)
    db.add(source)
    await db.commit()
    await db.refresh(source)

    logger.info(
        "Ingested source",
        source_id=source_id,
        total_chunks=len(chunks),
        new_chunks=len(deduped),
    )
    return source
