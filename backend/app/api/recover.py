"""Emergency data recovery endpoint — temporary, for restoring lost user data"""

import os
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

# Emergency recovery key (temporary)
RECOVERY_KEY = os.getenv("RECOVERY_KEY", "emergency-restore-nik")


@router.post("/admin/recover/nodes/{user_id}")
async def recover_nodes_for_user(user_id: str):
    """Assign all orphaned nodes to a user. Temporary — delete after use."""

    from ..db.database import get_database, NodeDB
    db = get_database()

    with db.session_scope() as session:
        # Find all nodes with NULL or mismatched user_id
        orphaned = session.query(NodeDB).filter(
            (NodeDB.user_id == None) | (NodeDB.user_id != user_id)
        ).all()

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


@router.get("/admin/recover/check/{user_id}")
async def check_orphaned_nodes(user_id: str):
    """Check how many nodes are orphaned for a user. Temporary — delete after use."""

    from ..db.database import get_database, NodeDB
    db = get_database()

    with db.session_scope() as session:
        from sqlalchemy import func
        orphaned = session.query(NodeDB).filter(
            (NodeDB.user_id == None) | (NodeDB.user_id != user_id)
        ).count()
        user_nodes = session.query(NodeDB).filter_by(user_id=user_id).count()
        # Status breakdown for user's nodes
        statuses = session.query(NodeDB.status, func.count(NodeDB.id)).filter_by(user_id=user_id).group_by(NodeDB.status).all()
        # All distinct user_ids in DB
        all_users = [r[0] for r in session.query(NodeDB.user_id).distinct().all()]

    return {
        "user_id": user_id,
        "orphaned_nodes": orphaned,
        "assigned_to_user": user_nodes,
        "status_breakdown": {s: c for s, c in statuses},
        "all_user_ids_in_db": all_users,
    }


@router.post("/admin/recover/reprocess/{user_id}")
async def reprocess_failed_nodes(request: Request, user_id: str):
    """Reset and synchronously reprocess all error nodes for a user."""
    import asyncio
    from ..db.database import get_database, NodeDB

    db = get_database()
    graph_service = request.app.state.graph_service

    with db.session_scope() as session:
        failed = session.query(NodeDB).filter_by(user_id=user_id, status="error").all()
        node_ids = [n.id for n in failed]
        for node in failed:
            node.status = "pending"
            node.error_message = None

    processed = 0
    errors = 0
    loop = asyncio.get_event_loop()
    for node_id in node_ids:
        try:
            await loop.run_in_executor(None, graph_service.process_node, node_id)
            processed += 1
        except Exception as e:
            errors += 1

    return {"processed": processed, "errors": errors, "total": len(node_ids)}
