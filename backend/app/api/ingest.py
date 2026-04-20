"""Ingestion API endpoints"""

import asyncio
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..db.database import get_database, NodeDB

logger = structlog.get_logger()
router = APIRouter()


class IngestRequest(BaseModel):
    url: str
    canvas_x: float = 100.0
    canvas_y: float = 100.0


@router.post("/ingest")
async def ingest_url(request: Request, body: IngestRequest):
    """Ingest a URL: detect type, extract content, create node, run agent in background."""
    # Duplicate check — return existing node immediately, skip re-ingestion
    dup_session = get_database().get_session()
    try:
        existing = dup_session.query(NodeDB).filter_by(url=body.url).first()
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
    finally:
        dup_session.close()

    try:
        ingestion_service = request.app.state.ingestion_service
        graph_service = request.app.state.graph_service

        # Synchronous extraction + node creation
        node = ingestion_service.ingest_url(
            url=body.url,
            canvas_x=body.canvas_x,
            canvas_y=body.canvas_y,
        )

        # Run agent processing in background (non-blocking)
        asyncio.get_event_loop().run_in_executor(
            None, graph_service.process_node, node.id
        )

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
async def get_ingest_status(request: Request, node_id: str):
    """Poll processing status for a node."""
    try:
        db = get_database()
        session = db.get_session()
        try:
            node = session.query(NodeDB).filter_by(id=node_id).first()
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
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
