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
        orphaned = session.query(NodeDB).filter(
            (NodeDB.user_id == None) | (NodeDB.user_id != user_id)
        ).count()

        user_nodes = session.query(NodeDB).filter_by(user_id=user_id).count()

    return {
        "user_id": user_id,
        "orphaned_nodes": orphaned,
        "assigned_to_user": user_nodes,
    }


@router.post("/admin/recover/reprocess/{user_id}")
async def reprocess_failed_nodes(request: Request, user_id: str):
    """Reset all error nodes for a user and re-trigger processing."""
    import asyncio
    from ..db.database import get_database, NodeDB

    db = get_database()
    graph_service = request.app.state.graph_service

    with db.session_scope() as session:
        failed = session.query(NodeDB).filter_by(user_id=user_id, status="error").all()
        node_ids = []
        for node in failed:
            node.status = "pending"
            node.error_message = None
            node_ids.append(node.id)

    loop = asyncio.get_event_loop()
    for node_id in node_ids:
        def _process(nid):
            try:
                graph_service.process_node(nid)
            except Exception:
                pass
        loop.run_in_executor(None, _process, node_id)

    return {"reprocessing": len(node_ids), "node_ids": node_ids}
