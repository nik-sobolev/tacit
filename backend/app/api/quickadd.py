"""Quick-add API — long-lived token for iOS Shortcuts and mobile capture"""

import uuid
import asyncio
import structlog
from fastapi import APIRouter, HTTPException, Request, Depends

from ..core.auth import get_current_user
from ..db.database import get_database, UserQuickTokenDB, NodeDB

logger = structlog.get_logger()
router = APIRouter()


@router.get("/quickadd/token")
async def get_or_create_token(current_user: dict = Depends(get_current_user)):
    """Get (or create) the user's long-lived quick-add token."""
    db = get_database()
    with db.session_scope() as session:
        existing = session.query(UserQuickTokenDB).filter_by(user_id=current_user["id"]).first()
        if existing:
            return {"token": existing.token, "user_id": current_user["id"]}
        token = str(uuid.uuid4())
        session.add(UserQuickTokenDB(token=token, user_id=current_user["id"]))
    return {"token": token, "user_id": current_user["id"]}


@router.post("/quickadd/rotate")
async def rotate_token(current_user: dict = Depends(get_current_user)):
    """Generate a new token (invalidates the old one)."""
    db = get_database()
    new_token = str(uuid.uuid4())
    with db.session_scope() as session:
        existing = session.query(UserQuickTokenDB).filter_by(user_id=current_user["id"]).first()
        if existing:
            existing.token = new_token
        else:
            session.add(UserQuickTokenDB(token=new_token, user_id=current_user["id"]))
    return {"token": new_token}


@router.get("/quickadd")
@router.post("/quickadd")
async def quick_add(request: Request, token: str, url: str = None):
    """Add a URL to the user's canvas. No Clerk auth — token-based."""
    db = get_database()

    with db.session_scope() as session:
        row = session.query(UserQuickTokenDB).filter_by(token=token).first()
        if not row:
            raise HTTPException(status_code=403, detail="Invalid token")
        user_id = row.user_id

    # Get URL from query param, form body, or JSON
    if not url:
        try:
            form = await request.form()
            url = form.get("url") or form.get("text")
        except Exception:
            pass
    if not url:
        try:
            body = await request.json()
            url = body.get("url") or body.get("text")
        except Exception:
            pass

    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="url required")

    # Duplicate check
    with db.session_scope() as session:
        existing = session.query(NodeDB).filter_by(url=url, user_id=user_id).first()
        if existing:
            return {"status": "duplicate", "node_id": existing.id}

    ingestion_service = request.app.state.ingestion_service
    graph_service = request.app.state.graph_service

    loop = asyncio.get_event_loop()
    node = await loop.run_in_executor(None, lambda: ingestion_service.ingest_url(url=url))

    # Tag with user_id
    with db.session_scope() as session:
        db_node = session.query(NodeDB).filter_by(id=node.id).first()
        if db_node:
            db_node.user_id = user_id

    def _process(node_id):
        try:
            graph_service.process_node(node_id)
        except Exception as e:
            logger.error("quickadd_process_failed", node_id=node_id, error=str(e))

    loop.run_in_executor(None, _process, node.id)

    logger.info("quickadd_success", user_id=user_id, url=url)
    return {"status": "queued", "node_id": node.id}
