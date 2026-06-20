"""Emergency data recovery endpoint — temporary, for restoring lost user data"""

import os
from fastapi import APIRouter, HTTPException, Request, Depends

router = APIRouter()

# Emergency recovery key — MUST be set via environment variable
# No default value — will fail at endpoint call if not configured
def get_recovery_key():
    key = os.getenv("RECOVERY_KEY")
    if not key:
        raise ValueError("RECOVERY_KEY environment variable is required for recovery endpoints")
    return key


@router.post("/admin/recover/nodes/{user_id}")
async def recover_nodes_for_user(user_id: str, request: Request):
    key = request.headers.get("X-Recovery-Key", "")
    if key != os.getenv("RECOVERY_KEY", ""):
        raise HTTPException(status_code=403, detail="Invalid key")

    from ..db.database import get_database, NodeDB
    db = get_database()

    with db.session_scope() as session:
        # ONLY assign nodes with NULL user_id — never touch nodes owned by another user
        orphaned = session.query(NodeDB).filter(NodeDB.user_id == None).all()

        count = 0
        for node in orphaned:
            node.user_id = user_id
            count += 1

        session.commit()

    return {
        "recovered": count,
        "user_id": user_id,
        "status": "nodes reassigned — refresh your canvas"
    }


@router.post("/admin/recover/conversations/{user_id}")
async def recover_conversations_for_user(user_id: str, request: Request):
    key = request.headers.get("X-Recovery-Key", "")
    if key != os.getenv("RECOVERY_KEY", ""):
        raise HTTPException(status_code=403, detail="Invalid key")

    from ..db.database import get_database, ConversationDB
    db = get_database()

    with db.session_scope() as session:
        orphaned = session.query(ConversationDB).filter(ConversationDB.user_id == None).all()
        count = 0
        for conv in orphaned:
            conv.user_id = user_id
            count += 1
        session.commit()

    return {"recovered": count, "user_id": user_id, "status": "conversations reassigned"}


@router.get("/admin/recover/check/{user_id}")
async def check_orphaned_nodes(user_id: str, request: Request):
    key = request.headers.get("X-Recovery-Key", "")
    if key != os.getenv("RECOVERY_KEY", ""):
        raise HTTPException(status_code=403, detail="Invalid key")

    from ..db.database import get_database, NodeDB
    db = get_database()

    with db.session_scope() as session:
        from sqlalchemy import func
        orphaned = session.query(NodeDB).filter(NodeDB.user_id == None).count()
        user_nodes = session.query(NodeDB).filter_by(user_id=user_id).count()
        # Status breakdown for user's nodes
        statuses = session.query(NodeDB.status, func.count(NodeDB.id)).filter_by(user_id=user_id).group_by(NodeDB.status).all()
        # All distinct user_ids in DB
        all_users = [r[0] for r in session.query(NodeDB.user_id).distinct().all()]
        # Sample error messages — must be inside session_scope
        error_samples = [
            {"id": n.id, "title": n.title, "error": n.error_message}
            for n in session.query(NodeDB).filter_by(user_id=user_id, status="error").limit(5).all()
        ]

    return {
        "user_id": user_id,
        "orphaned_nodes": orphaned,
        "assigned_to_user": user_nodes,
        "status_breakdown": {s: c for s, c in statuses},
        "all_user_ids_in_db": all_users,
        "error_samples": error_samples,
    }


@router.post("/admin/recover/reset-usage/{user_id}")
async def reset_usage(user_id: str, request: Request):
    key = request.headers.get("X-Recovery-Key", "")
    if key != os.getenv("RECOVERY_KEY", ""):
        raise HTTPException(status_code=403, detail="Invalid key")
    """Reset token usage counter for a user (admin tool)."""
    from ..db.database import get_database, UserUsageDB
    from datetime import datetime
    db = get_database()
    with db.session_scope() as session:
        usage = session.query(UserUsageDB).filter_by(user_id=user_id).first()
        if usage:
            usage.tokens_used = 0
            usage.period_start = datetime.utcnow()
    return {"reset": True, "user_id": user_id}


@router.post("/admin/recover/reprocess/{user_id}")
async def reprocess_failed_nodes(request: Request, user_id: str):
    """Reset failed nodes to pending and process them in background."""
    key = request.headers.get("X-Recovery-Key", "")
    if key != os.getenv("RECOVERY_KEY", ""):
        raise HTTPException(status_code=403, detail="Invalid key")
    import asyncio
    import threading
    from ..db.database import get_database, NodeDB

    db = get_database()
    graph_service = request.app.state.graph_service

    with db.session_scope() as session:
        failed = session.query(NodeDB).filter_by(user_id=user_id, status="error").all()
        node_ids = [n.id for n in failed]
        for node in failed:
            node.status = "pending"
            node.error_message = None

    def _process_all():
        for node_id in node_ids:
            try:
                graph_service.process_node(node_id)
            except Exception:
                pass

    threading.Thread(target=_process_all, daemon=True).start()

    return {"queued": len(node_ids), "message": "Processing in background — refresh canvas in 2-3 minutes"}
