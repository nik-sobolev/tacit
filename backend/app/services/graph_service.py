"""Graph service — Claude agent processing, auto-linking, graph retrieval"""

import re
import uuid
import json
import structlog
from typing import List, Dict, Any, Optional
from datetime import datetime

from anthropic import Anthropic

from ..db.database import get_database, NodeDB, EdgeDB
from .vector_service import VectorService

logger = structlog.get_logger()


class GraphService:
    """Manages the knowledge graph: Claude agent processing and node relationships."""

    def __init__(self, vector_service: VectorService, client: Anthropic, model: str):
        self.db = get_database()
        self.vector_service = vector_service
        self.client = client
        self.model = model

    # ==================== AGENT PROCESSING ====================

    def process_node(self, node_id: str) -> None:
        """Run Claude agent on a node: generate summary, tags, and auto-link to related nodes."""
        session = self.db.get_session()
        try:
            node = session.query(NodeDB).filter_by(id=node_id).first()
            if not node:
                logger.error("process_node_not_found", node_id=node_id)
                return

            # Get existing nodes for context (before adding this one to vector DB)
            existing = self.vector_service.search_nodes(node.content[:2000] if node.content else node.title or "", limit=5)
            existing_summary = "\n".join(
                f"- [{n['id']}] {n['metadata'].get('title', 'Untitled')}: {n['content'][:200]}"
                for n in existing
                if n['id'] != node_id
            )

            # Gather existing categories for consistency
            existing_categories = self._get_existing_categories(session)
            agent_result = self._run_agent(node, existing_summary, existing_categories)

            # Update node with agent results
            if agent_result.get("title") and not node.title:
                node.title = agent_result["title"][:500]
            if agent_result.get("summary"):
                node.summary = agent_result["summary"]
            if agent_result.get("tags"):
                node.tags = agent_result["tags"][:10]
            node.status = "done"
            node.processed_at = datetime.utcnow()

            # Store enriched metadata
            meta = dict(node.node_meta or {})
            if agent_result.get("key_entities"):
                meta["key_entities"] = agent_result["key_entities"]
            if agent_result.get("category"):
                meta["category"] = agent_result["category"]
            if agent_result.get("purpose"):
                meta["purpose"] = agent_result["purpose"]
            node.node_meta = meta

            session.commit()

            # Add to vector DB (use summary + content for richer embeddings)
            embed_text = f"{node.title or ''}\n{node.summary or ''}\n{(node.content or '')[:3000]}"
            self.vector_service.add_node(
                node_id=node_id,
                content=embed_text,
                metadata={
                    "title": node.title or "",
                    "type": node.type,
                    "url": node.url or "",
                    "tags": ", ".join(node.tags or []),
                    "created_at": node.created_at.isoformat() if node.created_at else "",
                    "category": meta.get("category", ""),
                    "purpose": meta.get("purpose", ""),
                }
            )

            # Create edges to related nodes
            connections = agent_result.get("connections", [])
            self._create_agent_edges(node_id, connections, session)

            # Also run vector-similarity auto-link now that this node is embedded
            self.auto_link(node_id)

            logger.info("node_processed", node_id=node_id, tags=node.tags)

        except Exception as e:
            logger.error("process_node_error", node_id=node_id, error=str(e))
            # Use a fresh session for error handling — the original may be broken
            try:
                session.rollback()
            except Exception:
                pass
            try:
                err_session = self.db.get_session()
                try:
                    node = err_session.query(NodeDB).filter_by(id=node_id).first()
                    if node:
                        node.status = "error"
                        node.error_message = str(e)[:500]
                        err_session.commit()
                finally:
                    err_session.close()
            except Exception as e2:
                logger.error("process_node_error_handler_failed", node_id=node_id, error=str(e2))
        finally:
            session.close()

    def _get_existing_categories(self, session) -> List[str]:
        """Get distinct categories already used across nodes."""
        nodes = session.query(NodeDB.node_meta).filter(NodeDB.status == "done").all()
        categories = set()
        for (meta,) in nodes:
            if meta and isinstance(meta, dict) and meta.get("category"):
                categories.add(meta["category"])
        return sorted(categories)

    def _run_agent(self, node: NodeDB, existing_summary: str, existing_categories: List[str] = None) -> Dict[str, Any]:
        """Call Claude with structured extraction prompt."""
        content_preview = (node.content or "")[:6000]
        existing_section = f"\n\nEXISTING NODES IN KNOWLEDGE GRAPH:\n{existing_summary}" if existing_summary else ""
        categories_hint = ""
        if existing_categories:
            categories_hint = f"\n\nEXISTING CATEGORIES IN USE: {', '.join(existing_categories)}\nPrefer reusing these categories when the content fits. Create a new category only if none of the existing ones apply."

        prompt = f"""You are analyzing a piece of content being added to a personal knowledge graph.

CONTENT TYPE: {node.type}
URL: {node.url or "N/A"}
CURRENT TITLE: {node.title or "Unknown"}
CONTENT:
{content_preview}
{existing_section}{categories_hint}

Return a JSON object with these exact fields:
{{
  "title": "concise title (max 80 chars)",
  "summary": "2-3 sentence summary of the key ideas",
  "category": "short category name (2-3 words max, e.g. AI Strategy, Trading, Developer Tools)",
  "purpose": "one sentence: why this content matters and what role it serves in a knowledge base",
  "tags": ["tag1", "tag2", "tag3"],
  "key_entities": ["person or org or concept"],
  "connections": [
    {{"node_id": "id-from-existing-nodes", "reason": "why they're related", "strength": 0.8}}
  ]
}}

Rules:
- title: improve the existing title if needed, keep it concise
- summary: focus on the most important ideas, be specific
- category: a short thematic label grouping this with similar content. Reuse existing categories when possible.
- purpose: explain what value this content adds — is it reference material, a learning resource, a strategic insight, a tool, inspiration, etc.?
- tags: 3-6 lowercase single-word or hyphenated tags
- key_entities: people, organizations, technologies, concepts mentioned
- connections: only include if there are genuinely related existing nodes (strength 0.6-1.0)
- Return ONLY the JSON object, no other text"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()
            # Strip markdown code blocks if present
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
            return json.loads(text)
        except Exception as e:
            logger.error("agent_call_error", error=str(e))
            return {"title": node.title, "summary": "", "tags": [], "key_entities": [], "connections": []}

    def _create_agent_edges(self, node_id: str, connections: List[Dict], session) -> None:
        """Create edges from Claude agent's suggested connections."""
        for conn in connections:
            target_id = conn.get("node_id")
            if not target_id or target_id == node_id:
                continue
            # Verify target exists
            target = session.query(NodeDB).filter_by(id=target_id).first()
            if not target:
                continue
            # Don't duplicate
            existing = session.query(EdgeDB).filter(
                ((EdgeDB.source_id == node_id) & (EdgeDB.target_id == target_id)) |
                ((EdgeDB.source_id == target_id) & (EdgeDB.target_id == node_id))
            ).first()
            if existing:
                continue
            edge = EdgeDB(
                id=str(uuid.uuid4()),
                source_id=node_id,
                target_id=target_id,
                relationship_type="semantic",
                strength=float(conn.get("strength", 0.7)),
                label=str(conn.get("reason", ""))[:200],
                auto_generated=True,
                created_at=datetime.utcnow(),
            )
            session.add(edge)
        session.commit()

    # ==================== AUTO-LINKING ====================

    def auto_link(self, node_id: str, threshold: float = 0.7) -> List[EdgeDB]:
        """Find semantically similar nodes and create edges."""
        session = self.db.get_session()
        try:
            node = session.query(NodeDB).filter_by(id=node_id).first()
            if not node:
                return []

            query = f"{node.title or ''} {node.summary or ''}"
            similar = self.vector_service.search_nodes(query, limit=6)

            created_edges = []
            for candidate in similar:
                cid = candidate["id"]
                if cid == node_id:
                    continue
                score = candidate.get("relevance_score", 0)
                if score < threshold:
                    continue
                # Check for existing edge
                existing = session.query(EdgeDB).filter(
                    ((EdgeDB.source_id == node_id) & (EdgeDB.target_id == cid)) |
                    ((EdgeDB.source_id == cid) & (EdgeDB.target_id == node_id))
                ).first()
                if existing:
                    continue
                edge = EdgeDB(
                    id=str(uuid.uuid4()),
                    source_id=node_id,
                    target_id=cid,
                    relationship_type="semantic",
                    strength=round(score, 2),
                    label=f"Similarity: {score:.0%}",
                    auto_generated=True,
                    created_at=datetime.utcnow(),
                )
                session.add(edge)
                created_edges.append(edge)

            session.commit()
            logger.info("auto_link_complete", node_id=node_id, edges_created=len(created_edges))
            return created_edges
        except Exception as e:
            logger.error("auto_link_error", node_id=node_id, error=str(e))
            return []
        finally:
            session.close()

    # ==================== GRAPH RETRIEVAL ====================

    def get_graph(self, types: Optional[List[str]] = None) -> Dict[str, Any]:
        """Return all nodes and edges for the canvas."""
        session = self.db.get_session()
        try:
            query = session.query(NodeDB)
            if types:
                query = query.filter(NodeDB.type.in_(types))
            nodes = query.all()

            node_ids = {n.id for n in nodes}
            edges = session.query(EdgeDB).filter(
                EdgeDB.source_id.in_(node_ids),
                EdgeDB.target_id.in_(node_ids)
            ).all()

            return {
                "nodes": [self._node_to_dict(n) for n in nodes],
                "edges": [self._edge_to_dict(e) for e in edges],
            }
        finally:
            session.close()

    def create_edge(
        self,
        source_id: str,
        target_id: str,
        relationship_type: str = "manual",
        strength: float = 1.0,
        label: str = "",
    ) -> EdgeDB:
        """Create a manual edge between two nodes."""
        session = self.db.get_session()
        try:
            edge = EdgeDB(
                id=str(uuid.uuid4()),
                source_id=source_id,
                target_id=target_id,
                relationship_type=relationship_type,
                strength=strength,
                label=label,
                auto_generated=False,
                created_at=datetime.utcnow(),
            )
            session.add(edge)
            session.commit()
            return edge
        finally:
            session.close()

    def delete_node_edges(self, node_id: str) -> None:
        """Delete all edges connected to a node."""
        session = self.db.get_session()
        try:
            session.query(EdgeDB).filter(
                (EdgeDB.source_id == node_id) | (EdgeDB.target_id == node_id)
            ).delete(synchronize_session=False)
            session.commit()
        finally:
            session.close()

    def delete_edge(self, edge_id: str) -> bool:
        """Delete a single edge by ID. Returns True if deleted."""
        session = self.db.get_session()
        try:
            count = session.query(EdgeDB).filter_by(id=edge_id).delete(synchronize_session=False)
            session.commit()
            return count > 0
        finally:
            session.close()

    def find_edge(self, source_id: str, target_id: str) -> Optional[EdgeDB]:
        """Find an edge between two nodes (checks both directions)."""
        session = self.db.get_session()
        try:
            return session.query(EdgeDB).filter(
                ((EdgeDB.source_id == source_id) & (EdgeDB.target_id == target_id)) |
                ((EdgeDB.source_id == target_id) & (EdgeDB.target_id == source_id))
            ).first()
        finally:
            session.close()

    # ==================== EDGE RETRIEVAL FOR CHAT ====================

    def get_edges_for_nodes(self, node_ids: List[str], limit: int = 15) -> List[Dict[str, Any]]:
        """Fetch edges where at least one endpoint is in node_ids, enriched with titles."""
        if not node_ids:
            return []
        session = self.db.get_session()
        try:
            edges = (
                session.query(EdgeDB)
                .filter((EdgeDB.source_id.in_(node_ids)) | (EdgeDB.target_id.in_(node_ids)))
                .order_by(EdgeDB.strength.desc())
                .limit(limit)
                .all()
            )

            all_ids = set()
            for e in edges:
                all_ids.add(e.source_id)
                all_ids.add(e.target_id)

            nodes = session.query(NodeDB.id, NodeDB.title).filter(NodeDB.id.in_(all_ids)).all()
            title_map = {n.id: n.title or "Untitled" for n in nodes}

            return [
                {
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "source_title": title_map.get(e.source_id, "Unknown"),
                    "target_title": title_map.get(e.target_id, "Unknown"),
                    "label": e.label or "related",
                    "relationship_type": e.relationship_type,
                    "strength": e.strength,
                }
                for e in edges
            ]
        finally:
            session.close()

    # ==================== HELPERS ====================

    def _node_to_dict(self, node: NodeDB) -> Dict[str, Any]:
        return {
            "id": node.id,
            "type": node.type,
            "title": node.title,
            "summary": node.summary,
            "url": node.url,
            "thumbnail_url": node.thumbnail_url,
            "canvas_x": node.canvas_x,
            "canvas_y": node.canvas_y,
            "status": node.status,
            "tags": node.tags or [],
            "metadata": node.node_meta or {},
            "created_at": node.created_at.isoformat() if node.created_at else None,
        }

    def _edge_to_dict(self, edge: EdgeDB) -> Dict[str, Any]:
        return {
            "id": edge.id,
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "relationship_type": edge.relationship_type,
            "strength": edge.strength,
            "label": edge.label,
            "auto_generated": edge.auto_generated,
        }


