"""Graph and node API endpoints"""

import structlog
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import func

from ..db.database import get_database, NodeDB, EdgeDB, ConversationDB, UserSettingsDB, UserDB
from ..core.auth import get_current_user
from datetime import datetime

logger = structlog.get_logger()
router = APIRouter()


class SettingsUpdate(BaseModel):
    user_name: Optional[str] = None
    user_role: Optional[str] = None
    organization: Optional[str] = None


@router.get("/settings")
async def get_settings(current_user: dict = Depends(get_current_user)):
    db = get_database()
    with db.session_scope() as s:
        row = s.query(UserSettingsDB).filter_by(id=current_user["id"]).first()
        if not row:
            return {"user_name": None, "user_role": None, "organization": None}
        return {"user_name": row.user_name, "user_role": row.user_role, "organization": row.organization}


@router.put("/settings")
async def update_settings(body: SettingsUpdate, request: Request, current_user: dict = Depends(get_current_user)):
    db = get_database()
    with db.session_scope() as s:
        row = s.query(UserSettingsDB).filter_by(id=current_user["id"]).first()
        if not row:
            row = UserSettingsDB(id=current_user["id"])
            s.add(row)
        if body.user_name is not None:
            row.user_name = body.user_name
        if body.user_role is not None:
            row.user_role = body.user_role
        if body.organization is not None:
            row.organization = body.organization
        row.updated_at = datetime.utcnow()
    try:
        engine = request.app.state.engine
        engine.config.user_name = body.user_name or engine.config.user_name
        engine.config.user_role = body.user_role or engine.config.user_role
        engine.config.user_organization = body.organization or engine.config.user_organization
    except Exception:
        pass
    return {"ok": True}


@router.get("/notes")
async def list_notes(current_user: dict = Depends(get_current_user)):
    """Return all note nodes for the current user."""
    db = get_database()
    with db.session_scope() as s:
        rows = (
            s.query(NodeDB)
            .filter(NodeDB.type == "note", NodeDB.user_id == current_user["id"])
            .order_by(NodeDB.created_at.desc())
            .all()
        )
        return {
            "notes": [
                {
                    "id": n.id,
                    "title": n.title or "Untitled",
                    "summary": n.summary or "",
                    "content": n.content or "",
                    "tags": n.tags or [],
                    "status": n.status,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in rows
            ]
        }


@router.get("/canvas/summary")
async def get_canvas_summary(current_user: dict = Depends(get_current_user)):
    """Lightweight canvas summary — {nodeCount, categories, topCategory} —
    for the canvas loading screen to personalize with before the full
    /graph payload (which includes content/tags/etc. for every node) has
    resolved. Only pulls node_meta, not the full row, to stay fast."""
    try:
        db = get_database()
        with db.session_scope() as session:
            uid = current_user["id"]
            node_count = session.query(func.count(NodeDB.id)).filter_by(user_id=uid).scalar() or 0
            rows = session.query(NodeDB.node_meta).filter_by(user_id=uid).all()
            cats = {}
            for (meta,) in rows:
                cat = (meta or {}).get("category", "Uncategorized") or "Uncategorized"
                cats[cat] = cats.get(cat, 0) + 1
            top_category = max(cats, key=cats.get) if cats else None
            return {
                "nodeCount": node_count,
                "categories": len(cats),
                "topCategory": top_category,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/categories")
async def get_categories(current_user: dict = Depends(get_current_user)):
    """Return categories for the current user's nodes."""
    try:
        db = get_database()
        with db.session_scope() as session:
            nodes = session.query(NodeDB).filter_by(status="done", user_id=current_user["id"]).all()
            cats = {}
            for n in nodes:
                cat = (n.node_meta or {}).get("category", "Uncategorized") or "Uncategorized"
                cats[cat] = cats.get(cat, 0) + 1
            return {"categories": [{"name": k, "count": v} for k, v in sorted(cats.items())]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/insights")
async def get_insights(current_user: dict = Depends(get_current_user)):
    """Return proactive insights for the current user."""
    try:
        db = get_database()
        with db.session_scope() as session:
            uid = current_user["id"]
            all_nodes = session.query(NodeDB).filter_by(status="done", user_id=uid).all()
            node_ids = {n.id for n in all_nodes}
            all_edges = session.query(EdgeDB).filter(
                EdgeDB.source_id.in_(node_ids),
                EdgeDB.target_id.in_(node_ids)
            ).all() if node_ids else []

            cats = {}
            for n in all_nodes:
                cat = (n.node_meta or {}).get("category", "Uncategorized") or "Uncategorized"
                cats[cat] = cats.get(cat, 0) + 1

            last_conv = session.query(ConversationDB).filter_by(user_id=uid).order_by(
                ConversationDB.last_activity.desc()
            ).first()
            last_visit = last_conv.last_activity if last_conv else None

            new_nodes = []
            if last_visit:
                new_nodes = [
                    {"title": n.title or n.url or "Untitled",
                     "category": (n.node_meta or {}).get("category", ""),
                     "created_at": n.created_at.isoformat() if n.created_at else ""}
                    for n in all_nodes
                    if n.created_at and n.created_at > last_visit
                ]

            connected_ids = set()
            for e in all_edges:
                connected_ids.add(e.source_id)
                connected_ids.add(e.target_id)
            orphans = [
                {"id": n.id, "title": n.title or n.url or "Untitled"}
                for n in all_nodes
                if n.id not in connected_ids
            ]

            return {
                "total_nodes": len(all_nodes),
                "total_edges": len(all_edges),
                "categories": cats,
                "new_since_last_visit": new_nodes,
                "orphan_nodes": orphans,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class NodeUpdateRequest(BaseModel):
    title: Optional[str] = None
    canvas_x: Optional[float] = None
    canvas_y: Optional[float] = None
    tags: Optional[List[str]] = None


class LinkRequest(BaseModel):
    label: Optional[str] = ""
    strength: Optional[float] = 0.8


@router.get("/graph")
async def get_graph(
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Return nodes and edges for the current user's canvas."""
    try:
        graph_service = request.app.state.graph_service
        from ..api.chat import _upsert_user
        _upsert_user(current_user, graph_service=graph_service)
        return graph_service.get_graph(user_id=current_user["id"])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes")
async def list_nodes(request: Request, current_user: dict = Depends(get_current_user), limit: int = 200, offset: int = 0):
    """List nodes for the current user."""
    try:
        db = get_database()
        with db.session_scope() as session:
            nodes = session.query(NodeDB).filter_by(user_id=current_user["id"]).offset(offset).limit(limit).all()
            graph_service = request.app.state.graph_service
            return {"nodes": [graph_service._node_to_dict(n) for n in nodes]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}")
async def get_node(request: Request, node_id: str, current_user: dict = Depends(get_current_user)):
    """Get full node data — enforces ownership."""
    try:
        db = get_database()
        with db.session_scope() as session:
            node = session.query(NodeDB).filter_by(id=node_id, user_id=current_user["id"]).first()
            if not node:
                raise HTTPException(status_code=404, detail="Node not found")
            graph_service = request.app.state.graph_service
            data = graph_service._node_to_dict(node)
            data["content"] = node.content
            return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/nodes/{node_id}")
async def update_node(request: Request, node_id: str, body: NodeUpdateRequest, current_user: dict = Depends(get_current_user)):
    """Update node — enforces ownership."""
    try:
        db = get_database()
        with db.session_scope() as session:
            node = session.query(NodeDB).filter_by(id=node_id, user_id=current_user["id"]).first()
            if not node:
                raise HTTPException(status_code=404, detail="Node not found")
            if body.title is not None:
                node.title = body.title[:500]
            if body.canvas_x is not None:
                node.canvas_x = body.canvas_x
            if body.canvas_y is not None:
                node.canvas_y = body.canvas_y
            if body.tags is not None:
                node.tags = body.tags
            graph_service = request.app.state.graph_service
            return graph_service._node_to_dict(node)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/nodes/{node_id}")
async def delete_node(request: Request, node_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a node — enforces ownership."""
    try:
        db = get_database()
        graph_service = request.app.state.graph_service

        # Verify ownership
        with db.session_scope() as session:
            node = session.query(NodeDB).filter_by(id=node_id, user_id=current_user["id"]).first()
            if not node:
                raise HTTPException(status_code=404, detail="Node not found")

        graph_service.delete_node_edges(node_id)

        try:
            graph_service.vector_service.delete_node(node_id)
        except Exception:
            pass

        with db.session_scope() as session:
            node = session.query(NodeDB).filter_by(id=node_id, user_id=current_user["id"]).first()
            if node:
                session.delete(node)

        return {"success": True, "node_id": node_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}/related")
async def get_related_nodes(request: Request, node_id: str, current_user: dict = Depends(get_current_user)):
    """Get nodes related to this node — enforces ownership."""
    try:
        db = get_database()
        with db.session_scope() as session:
            # Verify the source node belongs to current user
            node = session.query(NodeDB).filter_by(id=node_id, user_id=current_user["id"]).first()
            if not node:
                raise HTTPException(status_code=404, detail="Node not found")

            edges = session.query(EdgeDB).filter(
                (EdgeDB.source_id == node_id) | (EdgeDB.target_id == node_id)
            ).all()
            related_ids = set()
            for e in edges:
                related_ids.add(e.target_id if e.source_id == node_id else e.source_id)

            # Only return related nodes that also belong to current user
            nodes = session.query(NodeDB).filter(
                NodeDB.id.in_(related_ids),
                NodeDB.user_id == current_user["id"]
            ).all()
            graph_service = request.app.state.graph_service
            return {"nodes": [graph_service._node_to_dict(n) for n in nodes]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes/{node_id}/link/{target_id}")
async def link_nodes(request: Request, node_id: str, target_id: str, body: LinkRequest, current_user: dict = Depends(get_current_user)):
    """Create a manual edge between two nodes — enforces ownership of both."""
    try:
        db = get_database()
        with db.session_scope() as session:
            src = session.query(NodeDB).filter_by(id=node_id, user_id=current_user["id"]).first()
            tgt = session.query(NodeDB).filter_by(id=target_id, user_id=current_user["id"]).first()
            if not src or not tgt:
                raise HTTPException(status_code=404, detail="Node not found")
        graph_service = request.app.state.graph_service
        edge = graph_service.create_edge(
            source_id=node_id,
            target_id=target_id,
            relationship_type="manual",
            strength=body.strength or 0.8,
            label=body.label or "",
        )
        return graph_service._edge_to_dict(edge)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes/recategorize")
async def recategorize_nodes(request: Request, current_user: dict = Depends(get_current_user)):
    """Re-run agent on current user's nodes missing category metadata."""
    import asyncio
    try:
        db = get_database()
        graph_service = request.app.state.graph_service
        with db.session_scope() as session:
            nodes = session.query(NodeDB).filter_by(status="done", user_id=current_user["id"]).all()
            to_process = [n.id for n in nodes if not (n.node_meta or {}).get("category")]

        for node_id in to_process:
            asyncio.get_event_loop().run_in_executor(None, graph_service.process_node, node_id)

        return {"queued": len(to_process)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/edges/{edge_id}")
async def delete_edge(request: Request, edge_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a single edge — verifies it connects nodes owned by current user."""
    try:
        db = get_database()
        graph_service = request.app.state.graph_service

        # Verify edge connects nodes owned by this user
        with db.session_scope() as session:
            from ..db.database import EdgeDB as EDB
            edge = session.query(EDB).filter_by(id=edge_id).first()
            if not edge:
                raise HTTPException(status_code=404, detail="Edge not found")
            src = session.query(NodeDB).filter_by(id=edge.source_id, user_id=current_user["id"]).first()
            if not src:
                raise HTTPException(status_code=403, detail="Not your edge")

        deleted = graph_service.delete_edge(edge_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Edge not found")
        return {"success": True, "edge_id": edge_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
