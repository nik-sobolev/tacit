"""Admin recovery endpoints — protected by RECOVERY_KEY. Never touches cross-user data."""

import os
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def _check_key(request: Request):
    key = request.headers.get("X-Recovery-Key", "")
    if key != os.getenv("RECOVERY_KEY", ""):
        raise HTTPException(status_code=403, detail="Invalid key")


@router.get("/admin/recover/check/{user_id}")
async def check_user_nodes(user_id: str, request: Request):
    """Check node count and status for a specific user. Read-only."""
    _check_key(request)
    from ..db.database import get_database, NodeDB
    from sqlalchemy import func
    db = get_database()
    with db.session_scope() as session:
        user_nodes = session.query(NodeDB).filter_by(user_id=user_id).count()
        statuses = session.query(NodeDB.status, func.count(NodeDB.id)).filter_by(user_id=user_id).group_by(NodeDB.status).all()
        error_samples = [
            {"id": n.id, "title": n.title, "error": n.error_message}
            for n in session.query(NodeDB).filter_by(user_id=user_id, status="error").limit(5).all()
        ]
    return {
        "user_id": user_id,
        "assigned_to_user": user_nodes,
        "status_breakdown": {s: c for s, c in statuses},
        "error_samples": error_samples,
    }


@router.post("/admin/recover/reset-usage/{user_id}")
async def reset_usage(user_id: str, request: Request):
    """Reset token usage counter for a specific user."""
    _check_key(request)
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
    """Reprocess failed nodes for a specific user only."""
    _check_key(request)
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
    return {"queued": len(node_ids), "message": "Processing in background"}
