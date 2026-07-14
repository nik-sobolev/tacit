"""Ingestion API endpoints"""

import asyncio
import structlog
from datetime import datetime, timedelta
from urllib.parse import urlparse
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

from ..db.database import get_database, NodeDB
from ..core.auth import get_current_user
from ..core.entitlements import check_and_reserve, record_action
from ..services.ingestion_service import DEFERRED_EXTRACTION_TYPES

# process_node() runs in a background thread with no persistence or resume —
# a server restart (e.g. a deploy) mid-processing orphans the node forever at
# status="processing" with no processed_at. Normal processing finishes in
# well under a minute, so treat anything still "processing" past this as
# stuck rather than genuinely in flight.
STUCK_PROCESSING_AFTER = timedelta(minutes=5)

logger = structlog.get_logger()
router = APIRouter()


class IngestRequest(BaseModel):
    url: str
    canvas_x: float = 100.0
    canvas_y: float = 100.0


class NoteRequest(BaseModel):
    content: str
    title: str = None
    canvas_x: float = 300.0
    canvas_y: float = 300.0


@router.post("/ingest")
async def ingest_url(request: Request, body: IngestRequest, current_user: dict = Depends(get_current_user)):
    """Ingest a URL for the current user."""
    db = get_database()
    user_id = current_user["id"]

    # "save" is never hard-gated (see core/entitlements.py) — this call only
    # exists to record the action for shadow-mode validation before cutover.
    check_and_reserve(user_id, "save")

    # Per-user duplicate check
    with db.session_scope() as dup_session:
        existing = dup_session.query(NodeDB).filter_by(url=body.url, user_id=user_id).first()
        if existing:
            stuck_processing = (
                existing.status == "processing"
                and not existing.processed_at
                and existing.created_at
                and (datetime.utcnow() - existing.created_at) > STUCK_PROCESSING_AFTER
            )
            is_failed = (
                existing.status == "error"
                or (existing.title and "Content Unavailable" in existing.title)
                or stuck_processing
            )
            if not is_failed:
                return {
                    "node_id": existing.id,
                    "type": existing.type,
                    "title": existing.title,
                    "status": existing.status,
                    "canvas_x": existing.canvas_x,
                    "canvas_y": existing.canvas_y,
                    "thumbnail_url": existing.thumbnail_url,
                    "duplicate": True,
                }
            # Failed node — delete so it gets re-processed
            dup_session.delete(existing)

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
                if node.type in DEFERRED_EXTRACTION_TYPES:
                    # ingest_url() returned a fast placeholder for these types
                    # without extracting content — do that here, off the request
                    # path, since these are the content types with unpredictable,
                    # potentially slow extraction (see DEFERRED_EXTRACTION_TYPES
                    # and extract_deferred()).
                    ok = ingestion_service.extract_deferred(node_id, node.url, node.type)
                    if not ok:
                        return  # already marked status="error" with a message
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

        record_action(user_id, "save", dedupe_key=f"save:{node.id}")

        # process_node() runs detached in a background thread with no path back to
        # this request, so it can never itself return a 402 — the synthesis cap must
        # be checked here, before scheduling it. Saving never blocks (see check above);
        # only the downstream AI enrichment is skipped if the user is capped.
        try:
            check_and_reserve(user_id, "synthesis")
            loop.run_in_executor(None, _safe_process, node.id)
        except HTTPException:
            with db.session_scope() as s:
                n = s.query(NodeDB).filter_by(id=node.id).first()
                if n:
                    n.status = "saved_no_synthesis"
            node.status = "saved_no_synthesis"
            logger.info("synthesis_skipped_capped", node_id=node.id, user_id=user_id)

        return {
            "node_id": node.id,
            "type": node.type,
            "title": node.title,
            "status": node.status,
            "canvas_x": node.canvas_x,
            "canvas_y": node.canvas_y,
            "thumbnail_url": node.thumbnail_url,
        }

    except Exception as e:
        logger.error("ingest_error", url=body.url, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest/note")
async def create_note(body: NoteRequest, current_user: dict = Depends(get_current_user)):
    """Create a text note on the canvas."""
    import uuid
    from datetime import datetime

    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Note content cannot be empty")

    check_and_reserve(current_user["id"], "save")  # never blocks — see ingest_url()

    db = get_database()
    node_id = str(uuid.uuid4())
    title = (body.title or "").strip() or body.content[:80].split("\n")[0]

    try:
        with db.session_scope() as session:
            session.add(NodeDB(
                id=node_id,
                user_id=current_user["id"],
                type="note",
                title=title[:500],
                content=body.content.strip(),
                summary=None,
                canvas_x=body.canvas_x,
                canvas_y=body.canvas_y,
                status="done",
                created_at=datetime.utcnow(),
                processed_at=datetime.utcnow(),
            ))

        record_action(current_user["id"], "save", dedupe_key=f"save:{node_id}")

        return {
            "node_id": node_id,
            "type": "note",
            "title": title,
            "status": "done",
            "canvas_x": body.canvas_x,
            "canvas_y": body.canvas_y,
        }
    except Exception as e:
        logger.error("create_note_error", user_id=current_user["id"], error=str(e))
        raise HTTPException(status_code=500, detail="Failed to create note")


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
