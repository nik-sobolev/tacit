"""Graph and node API endpoints"""

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import func

from ..db.database import get_database, NodeDB, EdgeDB, ConversationDB

logger = structlog.get_logger()
router = APIRouter()


@router.get("/categories")
async def get_categories():
    """Return all categories with node counts."""
    try:
        db = get_database()
        session = db.get_session()
        try:
            nodes = session.query(NodeDB).filter_by(status="done").all()
            cats = {}
            for n in nodes:
                cat = (n.node_meta or {}).get("category", "Uncategorized") or "Uncategorized"
                cats[cat] = cats.get(cat, 0) + 1
            return {"categories": [{"name": k, "count": v} for k, v in sorted(cats.items())]}
        finally:
            session.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/insights")
async def get_insights():
    """Return proactive insights: new nodes, orphans, category stats."""
    try:
        db = get_database()
        session = db.get_session()
        try:
            all_nodes = session.query(NodeDB).filter_by(status="done").all()
            all_edges = session.query(EdgeDB).all()

            # Category counts
            cats = {}
            for n in all_nodes:
                cat = (n.node_meta or {}).get("category", "Uncategorized") or "Uncategorized"
                cats[cat] = cats.get(cat, 0) + 1

            # Find last conversation activity
            last_conv = session.query(ConversationDB).order_by(
                ConversationDB.last_activity.desc()
            ).first()
            last_visit = last_conv.last_activity if last_conv else None

            # Nodes added since last visit
            new_nodes = []
            if last_visit:
                new_nodes = [
                    {"title": n.title or n.url or "Untitled",
                     "category": (n.node_meta or {}).get("category", ""),
                     "created_at": n.created_at.isoformat() if n.created_at else ""}
                    for n in all_nodes
                    if n.created_at and n.created_at > last_visit
                ]

            # Orphan nodes (no edges)
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
        finally:
            session.close()
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
async def get_graph(request: Request):
    """Return all nodes and edges for the canvas."""
    try:
        graph_service = request.app.state.graph_service
        return graph_service.get_graph()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes")
async def list_nodes(request: Request, limit: int = 200, offset: int = 0):
    """List all nodes with pagination."""
    try:
        db = get_database()
        session = db.get_session()
        try:
            nodes = session.query(NodeDB).offset(offset).limit(limit).all()
            graph_service = request.app.state.graph_service
            return {"nodes": [graph_service._node_to_dict(n) for n in nodes]}
        finally:
            session.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}")
async def get_node(request: Request, node_id: str):
    """Get full node data including content."""
    try:
        db = get_database()
        session = db.get_session()
        try:
            node = session.query(NodeDB).filter_by(id=node_id).first()
            if not node:
                raise HTTPException(status_code=404, detail="Node not found")
            graph_service = request.app.state.graph_service
            data = graph_service._node_to_dict(node)
            data["content"] = node.content  # include full content
            return data
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/nodes/{node_id}")
async def update_node(request: Request, node_id: str, body: NodeUpdateRequest):
    """Update node title, canvas position, or tags."""
    try:
        db = get_database()
        session = db.get_session()
        try:
            node = session.query(NodeDB).filter_by(id=node_id).first()
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
            session.commit()
            graph_service = request.app.state.graph_service
            return graph_service._node_to_dict(node)
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/nodes/{node_id}")
async def delete_node(request: Request, node_id: str):
    """Delete a node, its edges, and its vector embedding."""
    try:
        db = get_database()
        graph_service = request.app.state.graph_service

        # Delete edges
        graph_service.delete_node_edges(node_id)

        # Delete from vector DB
        try:
            graph_service.vector_service.delete_node(node_id)
        except Exception:
            pass

        # Delete from SQL
        session = db.get_session()
        try:
            node = session.query(NodeDB).filter_by(id=node_id).first()
            if node:
                session.delete(node)
                session.commit()
        finally:
            session.close()

        return {"success": True, "node_id": node_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes/{node_id}/related")
async def get_related_nodes(request: Request, node_id: str):
    """Get nodes connected to this node via edges."""
    try:
        from ..db.database import EdgeDB
        db = get_database()
        session = db.get_session()
        try:
            edges = session.query(EdgeDB).filter(
                (EdgeDB.source_id == node_id) | (EdgeDB.target_id == node_id)
            ).all()
            related_ids = set()
            for e in edges:
                related_ids.add(e.target_id if e.source_id == node_id else e.source_id)
            nodes = session.query(NodeDB).filter(NodeDB.id.in_(related_ids)).all()
            graph_service = request.app.state.graph_service
            return {"nodes": [graph_service._node_to_dict(n) for n in nodes]}
        finally:
            session.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes/{node_id}/link/{target_id}")
async def link_nodes(request: Request, node_id: str, target_id: str, body: LinkRequest):
    """Create a manual edge between two nodes."""
    try:
        graph_service = request.app.state.graph_service
        edge = graph_service.create_edge(
            source_id=node_id,
            target_id=target_id,
            relationship_type="manual",
            strength=body.strength or 0.8,
            label=body.label or "",
        )
        return graph_service._edge_to_dict(edge)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/nodes/recategorize")
async def recategorize_nodes(request: Request):
    """Re-run agent on nodes missing category/purpose metadata."""
    import asyncio
    try:
        db = get_database()
        graph_service = request.app.state.graph_service
        session = db.get_session()
        try:
            nodes = session.query(NodeDB).filter_by(status="done").all()
            to_process = [
                n.id for n in nodes
                if not (n.node_meta or {}).get("category")
            ]
        finally:
            session.close()

        # Process in background
        for node_id in to_process:
            asyncio.get_event_loop().run_in_executor(
                None, graph_service.process_node, node_id
            )

        return {"queued": len(to_process), "total": len(nodes)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/edges/{edge_id}")
async def delete_edge(request: Request, edge_id: str):
    """Delete a single edge by ID."""
    try:
        graph_service = request.app.state.graph_service
        deleted = graph_service.delete_edge(edge_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Edge not found")
        return {"success": True, "edge_id": edge_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
