"""FastAPI dependency-injection bindings."""

from pathlib import Path
from typing import Annotated

from fastapi import Depends
from qdrant_client import QdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session

_qdrant_client: QdrantClient | None = None


def get_qdrant() -> QdrantClient:
    """Return a singleton Qdrant client configured from application settings.

    Supports three modes:
    - ``server``: connects to a remote/Docker Qdrant instance.
    - ``local``: persists vectors to a local directory (no server needed).
    - ``memory``: in-memory only (lost on restart, for tests).

    The client is created once and reused across requests to avoid
    file-lock contention in local mode.
    """
    global _qdrant_client
    if _qdrant_client is not None:
        return _qdrant_client

    if settings.qdrant_mode == "memory":
        _qdrant_client = QdrantClient(":memory:")
    elif settings.qdrant_mode == "local":
        local_path = Path(settings.qdrant_local_path)
        local_path.mkdir(parents=True, exist_ok=True)
        _qdrant_client = QdrantClient(path=str(local_path))
    else:
        _qdrant_client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)

    return _qdrant_client


SessionDep = Annotated[AsyncSession, Depends(get_session)]
QdrantDep = Annotated[QdrantClient, Depends(get_qdrant)]
