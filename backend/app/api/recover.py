"""Emergency data recovery endpoint — temporary, for restoring lost user data"""

import os
from fastapi import APIRouter, HTTPException

router = APIRouter()

# Only enable if recovery key is set
RECOVERY_KEY = os.getenv("RECOVERY_KEY")


@router.post("/admin/recover/nodes/{user_id}")
async def recover_nodes_for_user(user_id: str, key: str):
    """Assign all orphaned nodes to a user. Temporary — delete after use."""
    if not RECOVERY_KEY or key != RECOVERY_KEY:
        raise HTTPException(status_code=403, detail="Invalid recovery key")

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
async def check_orphaned_nodes(user_id: str, key: str):
    """Check how many nodes are orphaned for a user. Temporary — delete after use."""
    if not RECOVERY_KEY or key != RECOVERY_KEY:
        raise HTTPException(status_code=403, detail="Invalid recovery key")

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
