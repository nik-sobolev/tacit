"""Ingestion API endpoints"""

import asyncio
import structlog
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

from ..db.database import get_database, NodeDB
from ..core.auth import get_current_user

_UNSUPPORTED_HOSTS = {"x.com", "twitter.com", "t.co"}

logger = structlog.get_logger()
router = APIRouter()


class IngestRequest(BaseModel):
    url: str
    canvas_x: float = 100.0
    canvas_y: float = 100.0


@router.post("/ingest")
async def ingest_url(request: Request, body: IngestRequest, current_user: dict = Depends(get_current_user)):
    """Ingest a URL for the current user."""
    db = get_database()
    user_id = current_user["id"]

    host = urlparse(body.url).netloc.lower().replace("www.", "")
    if host in _UNSUPPORTED_HOSTS:
        raise HTTPException(
            status_code=400,
            detail="X/Twitter links can't be ingested (they require a login). Paste the text content into chat instead and Tacit will save it as a note."
        )

    # Per-user duplicate check
    with db.session_scope() as dup_session:
        existing = dup_session.query(NodeDB).filter_by(url=body.url, user_id=user_id).first()
        if existing:
            return {
                "node_id": existing.id,
                "type": existing.type,
                "title": existing.title,
                "status": existing.status,
                "canvas_x": existing.canvas_x,
                "canvas_y": existing.canvas_y,
                "duplicate": True,
            }

    ingestion_service = request.app.state.ingestion_service
    graph_service = request.app.state.graph_service

    try:
        loop = asyncio.get_event_loop()
        node = await loop.run_in_executor(
            None,
            lambda: ingestion_service.ingest_url(
                url=body.url,
                canvas_x=body.canvas_x,
                canvas_y=body.canvas_y,
                user_id=user_id,
            ),
        )

        def _safe_process(node_id: str):
            try:
                graph_service.process_node(node_id)
            except Exception as e:
                logger.error("graph_process_node_failed", node_id=node_id, error=str(e))
                try:
                    with db.session_scope() as s:
                        n = s.query(NodeDB).filter_by(id=node_id).first()
                        if n and n.status != "done":
                            n.status = "error"
                            n.error_message = f"Processing failed: {e}"
                except Exception as inner:
                    logger.error("graph_process_status_update_failed", node_id=node_id, error=str(inner))

        loop.run_in_executor(None, _safe_process, node.id)

        return {
            "node_id": node.id,
            "type": node.type,
            "title": node.title,
            "status": node.status,
            "canvas_x": node.canvas_x,
            "canvas_y": node.canvas_y,
        }

    except Exception as e:
        logger.error("ingest_error", url=body.url, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ingest/{node_id}/status")
async def get_ingest_status(request: Request, node_id: str, current_user: dict = Depends(get_current_user)):
    """Poll processing status for a node — enforces ownership."""
    try:
        with get_database().session_scope() as session:
            node = session.query(NodeDB).filter_by(id=node_id, user_id=current_user["id"]).first()
            if not node:
                raise HTTPException(status_code=404, detail="Node not found")
            return {
                "node_id": node.id,
                "status": node.status,
                "title": node.title,
                "summary": node.summary,
                "thumbnail_url": node.thumbnail_url,
                "tags": node.tags or [],
                "error_message": node.error_message,
                "processed_at": node.processed_at.isoformat() if node.processed_at else None,
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
