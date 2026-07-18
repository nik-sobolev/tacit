"""Graph service — Claude agent processing, auto-linking, graph retrieval"""

import re
import uuid
import json
import structlog
import httpx
from typing import List, Dict, Any, Optional
from datetime import datetime

from anthropic import Anthropic

from ..db.database import get_database, NodeDB, EdgeDB, filter_owned_ids
from .vector_service import VectorService

logger = structlog.get_logger()

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


class GraphService:
    """Manages the knowledge graph: Claude agent processing and node relationships."""

    def __init__(
        self,
        vector_service: VectorService,
        client: Anthropic,
        model: str,
        gemini_api_key: str = "",
        summarization_provider: str = "gemini",
    ):
        self.db = get_database()
        self.vector_service = vector_service
        self.client = client
        self.model = model
        self.gemini_api_key = gemini_api_key
        # Falls back to Claude if Gemini isn't configured, regardless of the setting —
        # so an unset GEMINI_API_KEY can't silently break node enrichment.
        self.summarization_provider = summarization_provider if gemini_api_key else "claude"

    # ==================== AGENT PROCESSING ====================

    def process_node(self, node_id: str) -> None:
        """Run Claude agent on a node: generate summary, tags, and auto-link to related nodes."""
        try:
            session = self.db.get_session()
            try:
                node = session.query(NodeDB).filter_by(id=node_id).first()
                if not node:
                    logger.error("process_node_not_found", node_id=node_id)
                    return
                if node.status == "error":
                    logger.info("process_node_skip_errored_node", node_id=node_id)
                    return

                # Get existing nodes for context (before adding this one to vector DB).
                # Scoped to this user's nodes at the query level, but still re-verified
                # in SQL below — legacy nodes indexed before user_id was added to Chroma
                # metadata won't match the filter and would otherwise leak across tenants.
                existing = self.vector_service.search_nodes(
                    node.content[:2000] if node.content else node.title or "",
                    limit=5,
                    filter={"user_id": node.user_id or ""},
                )
                existing_ids = {n["id"] for n in existing if n["id"] != node_id}
                owned_existing_ids = filter_owned_ids(session, NodeDB, existing_ids, node.user_id)
                existing_summary = "\n".join(
                    f"- [{n['id']}] {n['metadata'].get('title', 'Untitled')}: {n['content'][:200]}"
                    for n in existing
                    if n['id'] in owned_existing_ids
                )

                # Gather existing categories for consistency
                existing_categories = self._get_existing_categories(session)
                agent_result = self._run_agent(node, existing_summary, existing_categories)

                node_type = node.type
                node_url = node.url or ""
                node_content = node.content or ""
                node_created_at = node.created_at
                node_title = node.title
                node_meta_in = dict(node.node_meta or {})
                node_user_id = node.user_id
            finally:
                session.close()

            title_out = (agent_result.get("title") or node_title or "")[:500]
            summary_out = agent_result.get("summary") or ""
            tags_out = (agent_result.get("tags") or [])[:10]
            meta = node_meta_in
            if agent_result.get("key_entities"):
                meta["key_entities"] = agent_result["key_entities"]
            if agent_result.get("category"):
                meta["category"] = agent_result["category"]
            if agent_result.get("purpose"):
                meta["purpose"] = agent_result["purpose"]
            if agent_result.get("key_points"):
                meta["key_points"] = agent_result["key_points"]
            content_out = (node_content or "")[:3000]
            processed_at = datetime.utcnow()

            # Save via the retry-aware helper — the DB write is the one step that has
            # been observed to fail with transient errors ("disk I/O error" / "database
            # is locked" on SQLite; connection blips on Postgres). run_with_retry
            # recycles the engine and retries once instead of losing the agent's work.
            def _save(s):
                n = s.query(NodeDB).filter_by(id=node_id).first()
                if not n:
                    return
                n.title = title_out
                n.summary = summary_out
                n.status = "done"
                n.tags = tags_out
                n.node_meta = meta
                n.processed_at = processed_at

            self.db.run_with_retry(_save)

            # Add to vector DB
            embed_text = f"{title_out}\n{summary_out}\n{content_out}"
            self.vector_service.add_node(
                node_id=node_id,
                content=embed_text,
                metadata={
                    "title": title_out,
                    "type": node_type,
                    "url": node_url,
                    "tags": ", ".join(tags_out),
                    "created_at": node_created_at.isoformat() if node_created_at else "",
                    "category": meta.get("category", ""),
                    "purpose": meta.get("purpose", ""),
                    "user_id": node_user_id or "",
                }
            )

            # Create edges to related nodes (fresh session — original was closed above)
            connections = agent_result.get("connections", [])
            edge_session = self.db.get_session()
            try:
                self._create_agent_edges(node_id, connections, edge_session)
            finally:
                edge_session.close()

            # Also run vector-similarity auto-link now that this node is embedded
            self.auto_link(node_id)

            logger.info("node_processed", node_id=node_id, tags=tags_out)

        except Exception as e:
            logger.error("process_node_error", node_id=node_id, error=str(e))
            try:
                def _mark_error(s):
                    n = s.query(NodeDB).filter_by(id=node_id).first()
                    if n:
                        n.status = "error"
                        n.error_message = str(e)[:500]

                self.db.run_with_retry(_mark_error)
            except Exception as e2:
                logger.error("process_node_error_handler_failed", node_id=node_id, error=str(e2))

    def _get_existing_categories(self, session) -> List[str]:
        """Get distinct categories already used across nodes."""
        nodes = session.query(NodeDB.node_meta).filter(NodeDB.status == "done").all()
        categories = set()
        for (meta,) in nodes:
            if meta and isinstance(meta, dict) and meta.get("category"):
                categories.add(meta["category"])
        return sorted(categories)

    def _run_agent(self, node: NodeDB, existing_summary: str, existing_categories: List[str] = None) -> Dict[str, Any]:
        """Run the structured-extraction prompt against the configured provider."""
        prompt = self._build_agent_prompt(node, existing_summary, existing_categories)
        if self.summarization_provider == "gemini":
            return self._call_gemini(prompt, node)
        return self._call_claude(prompt, node)

    def _build_agent_prompt(self, node: NodeDB, existing_summary: str, existing_categories: List[str] = None) -> str:
        content_preview = (node.content or "")[:6000]
        existing_section = f"\n\nEXISTING NODES IN KNOWLEDGE GRAPH:\n{existing_summary}" if existing_summary else ""
        categories_hint = ""
        if existing_categories:
            categories_hint = f"\n\nEXISTING CATEGORIES IN USE: {', '.join(existing_categories)}\nPrefer reusing these categories when the content fits. Create a new category only if none of the existing ones apply."

        is_video = node.type in ("youtube", "tiktok", "instagram")
        key_points_field = """
  "key_points": ["specific insight 1", "specific insight 2", "...up to 10 bullets"],""" if is_video else ""
        key_points_rule = """
- key_points: 6-10 bullet strings of the most important specific insights — include names, companies, numbers, specific claims. Lead with the sharpest insight. No filler.""" if is_video else ""

        prompt = f"""You are analyzing a piece of content being added to a personal knowledge graph.

CONTENT TYPE: {node.type}
URL: {node.url or "N/A"}
CURRENT TITLE: {node.title or "Unknown"}
CONTENT:
{content_preview}
{existing_section}{categories_hint}

Return a JSON object with these exact fields:
{{{key_points_field}
  "title": "concise title (max 80 chars)",
  "summary": "2-3 sentence summary identifying the speaker/source and key themes",
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
- summary: focus on the most important ideas, be specific{key_points_rule}
- category: a short thematic label grouping this with similar content. Reuse existing categories when possible.
- purpose: explain what value this content adds — is it reference material, a learning resource, a strategic insight, a tool, inspiration, etc.?
- tags: 3-6 lowercase single-word or hyphenated tags
- key_entities: people, organizations, technologies, concepts mentioned
- connections: only include if there are genuinely related existing nodes (strength 0.6-1.0)
- Return ONLY the JSON object, no other text"""
        return prompt

    @staticmethod
    def _parse_json_response(text: str) -> Dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        return json.loads(text)

    def _call_claude(self, prompt: str, node: NodeDB) -> Dict[str, Any]:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}]
            )
            text = response.content[0].text.strip()

            # Record token usage (usage v1 — kept running through the shadow period)
            if node.user_id and response.usage:
                from ..core.usage import record_usage
                record_usage(node.user_id, response.usage.input_tokens, response.usage.output_tokens)

                from ..core.entitlements import record_action
                record_action(
                    node.user_id, "synthesis", dedupe_key=f"synthesis:{node.id}",
                    input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                )

            return self._parse_json_response(text)
        except Exception as e:
            # Do NOT swallow this into a fake-success fallback -- that previously let
            # process_node mark status="done" with an empty summary/no key_points and
            # no error anywhere, on ANY agent failure (truncated JSON, API error,
            # refusal). Re-raise so process_node's own except (which sets
            # status="error" + error_message) actually runs, making the failure visible
            # and letting it fall into the existing stuck/failed-node retry path.
            logger.error("agent_call_error", provider="claude", error=str(e))
            raise

    def _call_gemini(self, prompt: str, node: NodeDB) -> Dict[str, Any]:
        try:
            response = httpx.post(
                GEMINI_URL,
                params={"key": self.gemini_api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 3000},
                },
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            candidates = data.get("candidates") or []
            if not candidates:
                raise ValueError(f"Gemini returned no candidates: {json.dumps(data)[:300]}")
            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            # Record synthesis usage — this path had zero tracking before usage v2
            # (the token-based usage.py never covered Gemini calls at all).
            if node.user_id:
                usage_meta = data.get("usageMetadata", {})
                from ..core.entitlements import record_action
                record_action(
                    node.user_id, "synthesis", dedupe_key=f"synthesis:{node.id}",
                    input_tokens=usage_meta.get("promptTokenCount", 0),
                    output_tokens=usage_meta.get("candidatesTokenCount", 0),
                )

            return self._parse_json_response(text)
        except Exception as e:
            # Same reasoning as _call_claude: let this propagate to process_node's
            # except so a Gemini failure marks the node status="error" instead of
            # silently completing with an empty summary.
            logger.error("agent_call_error", provider="gemini", error=str(e))
            raise

    def _create_agent_edges(self, node_id: str, connections: List[Dict], session) -> None:
        """Create edges from Claude agent's suggested connections. Only links nodes
        that share an owner with node_id — the agent's candidate connections come
        from an unscoped ChromaDB search (see process_node), so this is where a
        cross-tenant "connection" would otherwise turn into a real EdgeDB row."""
        source_user_id = session.query(NodeDB.user_id).filter_by(id=node_id).scalar()
        for conn in connections:
            target_id = conn.get("node_id")
            if not target_id or target_id == node_id:
                continue
            # Verify target exists and belongs to the same user
            target = session.query(NodeDB).filter_by(id=target_id).first()
            if not target or target.user_id != source_user_id:
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
        """Find semantically similar nodes and create edges. search_nodes is scoped
        to node_id's own user, but candidates are still re-verified as owned in SQL
        before any edge gets created — legacy nodes indexed before user_id was added
        to Chroma metadata won't match the filter and would otherwise leak across tenants."""
        session = self.db.get_session()
        try:
            node = session.query(NodeDB).filter_by(id=node_id).first()
            if not node:
                return []

            query = f"{node.title or ''} {node.summary or ''}"
            similar = self.vector_service.search_nodes(
                query, limit=6, filter={"user_id": node.user_id or ""}
            )
            candidate_ids = {c["id"] for c in similar if c["id"] != node_id}
            owned_candidate_ids = filter_owned_ids(session, NodeDB, candidate_ids, node.user_id)

            created_edges = []
            for candidate in similar:
                cid = candidate["id"]
                if cid == node_id or cid not in owned_candidate_ids:
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

    def get_graph(self, user_id: str = None, types: Optional[List[str]] = None) -> Dict[str, Any]:
        """Return nodes and edges for a specific user's canvas."""
        session = self.db.get_session()
        try:
            query = session.query(NodeDB)
            if user_id:
                query = query.filter(NodeDB.user_id == user_id)
            if types:
                query = query.filter(NodeDB.type.in_(types))
            nodes = query.all()

            node_ids = {n.id for n in nodes}
            edges = session.query(EdgeDB).filter(
                EdgeDB.source_id.in_(node_ids),
                EdgeDB.target_id.in_(node_ids)
            ).all() if node_ids else []

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

    def get_edges_for_nodes(self, node_ids: List[str], user_id: str = None, limit: int = 15) -> List[Dict[str, Any]]:
        """Fetch edges where at least one endpoint is in node_ids, enriched with titles.

        EdgeDB has no user_id column of its own, so ownership is derived from both
        endpoints' NodeDB rows. This is the read-side backstop: auto_link/
        _create_agent_edges are the write-side guard against creating cross-tenant
        edges, but this also drops any edge created before that guard existed —
        an edge only comes back if BOTH endpoints are owned by user_id.
        """
        if not node_ids or not user_id:
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

            owned_ids = filter_owned_ids(session, NodeDB, all_ids, user_id)
            title_map = {
                r.id: r.title or "Untitled"
                for r in session.query(NodeDB.id, NodeDB.title).filter(NodeDB.id.in_(owned_ids)).all()
            }

            return [
                {
                    "source_id": e.source_id,
                    "target_id": e.target_id,
                    "source_title": title_map[e.source_id],
                    "target_title": title_map[e.target_id],
                    "label": e.label or "related",
                    "relationship_type": e.relationship_type,
                    "strength": e.strength,
                }
                for e in edges
                if e.source_id in title_map and e.target_id in title_map
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
            "error_message": node.error_message,
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


