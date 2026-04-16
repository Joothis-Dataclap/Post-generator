"""Sources API — file upload and direct text ingestion endpoints."""

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from sqlalchemy import select

from app.core.dependencies import QdrantDep, SessionDep
from app.models.source import Source
from app.schemas.source import SourceDetailResponse, SourceResponse
from app.services.ingestion import ingest_source
from app.services.retrieval import get_chunks_for_source

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[SourceResponse])
async def list_sources(db: SessionDep) -> list[Source]:
    """List all ingested source documents, newest first."""
    result = await db.execute(select(Source).order_by(Source.created_at.desc()))
    return list(result.scalars().all())


@router.get("/{source_id}", response_model=SourceDetailResponse)
async def get_source(source_id: str, db: SessionDep, qdrant: QdrantDep) -> SourceDetailResponse:
    """Get one source with its raw text and all associated chunks."""
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    chunks = get_chunks_for_source(qdrant, source_id)
    return SourceDetailResponse(
        id=source.id,
        title=source.title,
        source_type=source.source_type,
        category=source.category,
        filename=source.filename,
        chunk_count=source.chunk_count,
        created_at=source.created_at,
        raw_text=source.raw_text,
        chunks=chunks,
    )


@router.delete("/{source_id}", status_code=204)
async def delete_source(source_id: str, db: SessionDep) -> None:
    """Delete a source document and all its metadata.

    Note: Qdrant chunks are not removed here — run a vector-store cleanup
    job separately if storage reclamation is required.
    """
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    await db.delete(source)
    await db.commit()


@router.post("", response_model=SourceResponse, status_code=201)
async def create_source(
    db: SessionDep,
    qdrant: QdrantDep,
    title: str = Form(...),
    source_type: str = Form("article"),
    category: str = Form("general"),
    text_content: str | None = Form(None),
    file: UploadFile | None = File(None),
) -> Source:
    """Ingest a new source document.

    Accepts either a file upload (PDF, DOCX, TXT, MD, HTML) or
    direct ``text_content``. At least one must be provided.
    """
    file_bytes: bytes | None = None
    filename: str | None = None

    if file:
        file_bytes = await file.read()
        filename = file.filename

    if not text_content and not file_bytes:
        raise HTTPException(
            status_code=400,
            detail="Provide either text_content or a file upload",
        )

    try:
        source = await ingest_source(
            db=db,
            qdrant=qdrant,
            title=title,
            source_type=source_type,
            category=category,
            filename=filename,
            file_bytes=file_bytes,
            text_content=text_content,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return source
