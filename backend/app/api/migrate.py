"""Bulk migration endpoint — protected by secret header, bypasses Clerk auth"""

import os
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()

def get_migration_config():
    """Lazy-load migration config - fail only if endpoints are called"""
    secret = os.getenv("MIGRATION_SECRET")
    user_id = os.getenv("MIGRATION_USER_ID")
    if not secret:
        raise ValueError("MIGRATION_SECRET environment variable is required")
    if not user_id:
        raise ValueError("MIGRATION_USER_ID environment variable is required")
    return secret, user_id


class ContextItem(BaseModel):
    title: str
    type: Optional[str] = "insight"
    content: Optional[str] = ""
    tags: Optional[List[str]] = []


class MigrateRequest(BaseModel):
    urls: List[str] = []
    contexts: List[ContextItem] = []


@router.post("/migrate")
async def migrate(request: Request, body: MigrateRequest):
    MIGRATION_SECRET, MIGRATION_USER_ID = get_migration_config()
    secret = request.headers.get("X-Migration-Secret", "")
    if secret != MIGRATION_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    from ..db.database import get_database, NodeDB, ContextDB
    from ..services.ingestion_service import DEFERRED_EXTRACTION_TYPES, DEFERRED_EXTRACTION_EXECUTOR

    db = get_database()
    ingestion_service = request.app.state.ingestion_service
    graph_service = request.app.state.graph_service

    import asyncio
    loop = asyncio.get_event_loop()

    queued_urls = 0
    skipped = 0
    failed_urls = []
    created_contexts = 0

    # Get existing URLs to skip duplicates
    with db.session_scope() as session:
        existing_urls = {n.url for n in session.query(NodeDB).filter(NodeDB.url.isnot(None)).all()}

    for i, url in enumerate(body.urls):
        if url in existing_urls:
            skipped += 1
            continue
        try:
            def _ingest_and_tag(u, idx):
                n = ingestion_service.ingest_url(
                    url=u,
                    canvas_x=100.0 + (idx % 5) * 320,
                    canvas_y=100.0 + (idx // 5) * 260,
                )
                # Set user_id immediately in same thread
                with db.session_scope() as s:
                    db_node = s.query(NodeDB).filter_by(id=n.id).first()
                    if db_node:
                        db_node.user_id = MIGRATION_USER_ID
                return n

            node = await loop.run_in_executor(None, _ingest_and_tag, url, i)

            def _process(node_id):
                try:
                    if node.type in DEFERRED_EXTRACTION_TYPES:
                        if not ingestion_service.extract_deferred(node_id, node.url, node.type):
                            return
                    graph_service.process_node(node_id)
                except Exception:
                    pass

            loop.run_in_executor(DEFERRED_EXTRACTION_EXECUTOR, _process, node.id)
            queued_urls += 1
        except Exception as e:
            failed_urls.append({"url": url, "error": str(e)})

    # Insert contexts
    for ctx in body.contexts:
        try:
            with db.session_scope() as session:
                existing = session.query(ContextDB).filter_by(title=ctx.title).first()
                if existing:
                    continue
                c = ContextDB(
                    id=str(uuid.uuid4()),
                    title=ctx.title,
                    type=ctx.type or "insight",
                    content=ctx.content or "",
                    tags=ctx.tags or [],
                    created_at=datetime.utcnow(),
                )
                session.add(c)
                created_contexts += 1
        except Exception:
            pass

    return {
        "queued_urls": queued_urls,
        "skipped_duplicates": skipped,
        "failed_urls": failed_urls,
        "created_contexts": created_contexts,
        "user_id": MIGRATION_USER_ID,
    }
