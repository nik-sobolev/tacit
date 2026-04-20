"""Tacit Twin Engine - Combines coaching, context, and document intelligence"""

import json
import re
import time
import structlog
import uuid
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from anthropic import Anthropic, APIStatusError

from .config import TacitConfig
from ..services.vector_service import VectorService
from ..models.chat import ChatMode
from ..db.database import get_database, ConversationDB, MessageDB, NodeDB, EdgeDB, PersonDB

logger = structlog.get_logger()


class TacitEngine:
    """
    Main Tacit engine that orchestrates:
    - Executive coaching
    - Context retrieval
    - Document search
    - Unified intelligent responses
    """

    def __init__(self, config: Optional[TacitConfig] = None, graph_service=None):
        """Initialize the Tacit engine"""

        self.config = config or TacitConfig.load()
        self.client = Anthropic(api_key=self.config.anthropic_api_key)
        self.vector_service = VectorService(self.config.chroma_persist_dir)
        self.db = get_database()
        self.graph_service = graph_service

        # In-memory cache: session_id -> list of message dicts
        # Populated lazily from DB on first access
        self.conversations: Dict[str, List[Dict[str, Any]]] = {}

        logger.info(
            "tacit_engine_initialized",
            model=self.config.default_model,
            user=self.config.user_name
        )

    # ==================== DB HELPERS ====================

    def _ensure_conversation(self, session_id: str) -> None:
        """Create a ConversationDB record if one doesn't exist yet."""
        session = self.db.get_session()
        try:
            if not session.query(ConversationDB).filter_by(id=session_id).first():
                session.add(ConversationDB(id=session_id))
                session.commit()
        finally:
            session.close()

    def _load_from_db(self, session_id: str) -> List[Dict[str, Any]]:
        """Load all messages for a session from the DB."""
        session = self.db.get_session()
        try:
            rows = (
                session.query(MessageDB)
                .filter_by(conversation_id=session_id)
                .order_by(MessageDB.timestamp)
                .all()
            )
            result = []
            for m in rows:
                entry: Dict[str, Any] = {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                }
                if m.mode:
                    entry["mode"] = m.mode
                if m.sources:
                    entry["sources"] = m.sources
                result.append(entry)
            return result
        finally:
            session.close()

    def _persist_message(
        self,
        session_id: str,
        role: str,
        content: str,
        mode: Optional[str] = None,
        sources: Optional[List] = None,
    ) -> None:
        """Write a single message to DB and bump conversation metadata."""
        session = self.db.get_session()
        try:
            session.add(MessageDB(
                id=str(uuid.uuid4()),
                conversation_id=session_id,
                role=role,
                content=content,
                timestamp=datetime.utcnow(),
                mode=mode,
                sources=sources or [],
            ))
            conv = session.query(ConversationDB).filter_by(id=session_id).first()
            if conv:
                conv.last_activity = datetime.utcnow()
                conv.message_count = (conv.message_count or 0) + 1
            session.commit()
        finally:
            session.close()

    # ==================== PUBLIC API ====================

    def process_message(
        self,
        session_id: str,
        user_message: str,
        mode: Optional[ChatMode] = None
    ) -> Dict[str, Any]:
        """
        Process a user message and generate twin response.

        Args:
            session_id: Session identifier
            user_message: Message from user
            mode: Optional chat mode (general, coaching, query)

        Returns:
            Dict with response, sources, and metadata
        """
        # Ensure a DB record exists for this session
        self._ensure_conversation(session_id)

        # Populate in-memory cache from DB if this is the first access
        if session_id not in self.conversations:
            self.conversations[session_id] = self._load_from_db(session_id)

        # Persist + cache the user message
        self._persist_message(session_id, "user", user_message)
        self.conversations[session_id].append({
            "role": "user",
            "content": user_message,
            "timestamp": datetime.utcnow(),
        })

        # Determine mode if not specified
        if mode is None:
            mode = self._determine_mode(user_message)

        # Retrieve relevant context and documents
        is_temporal = self._is_temporal_query(user_message)
        knowledge = self._retrieve_knowledge(user_message)

        # For temporal queries, also add a chronologically sorted node list
        if is_temporal:
            knowledge["recent_nodes"] = self._get_recent_nodes(limit=10)

        # Always inject complete canvas inventory so chat sees all nodes in real-time
        knowledge["canvas_nodes"] = self._get_all_canvas_nodes()

        # Add orphan nodes context
        knowledge["orphan_nodes"] = self._get_orphan_nodes()

        # Build enhanced prompt with knowledge
        system_prompt = self._build_prompt(mode, knowledge)

        # People tools always on; canvas tools only when linking intent detected
        enable_canvas_tools = self._has_linking_intent(user_message)

        # Generate response
        response_text, actions = self._generate_response(
            system_prompt,
            self.conversations[session_id],
            enable_canvas_tools=enable_canvas_tools
        )

        # Persist + cache the assistant response
        mode_str = mode.value if hasattr(mode, "value") else str(mode)
        self._persist_message(
            session_id, "assistant", response_text,
            mode=mode_str, sources=knowledge.get("sources", [])
        )
        self.conversations[session_id].append({
            "role": "assistant",
            "content": response_text,
            "timestamp": datetime.utcnow(),
            "mode": mode,
            "sources": knowledge.get("sources", []),
        })

        logger.info(
            "message_processed",
            session_id=session_id,
            mode=mode,
            sources_count=len(knowledge.get("sources", [])),
            actions_count=len(actions)
        )

        return {
            "response": response_text,
            "mode": mode,
            "sources": knowledge.get("sources", []),
            "actions": actions,
            "metadata": {
                "contexts_found": len(knowledge.get("contexts", [])),
                "documents_found": len(knowledge.get("documents", [])),
            },
        }

    def get_conversation(self, session_id: str) -> List[Dict[str, Any]]:
        """Get conversation history for a session (from cache or DB)."""
        if session_id not in self.conversations:
            self.conversations[session_id] = self._load_from_db(session_id)
        return self.conversations[session_id]

    def clear_conversation(self, session_id: str) -> None:
        """Delete conversation and all its messages."""
        session = self.db.get_session()
        try:
            session.query(MessageDB).filter_by(conversation_id=session_id).delete()
            session.query(ConversationDB).filter_by(id=session_id).delete()
            session.commit()
        finally:
            session.close()

        self.conversations.pop(session_id, None)
        logger.info("conversation_cleared", session_id=session_id)

    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        vector_stats = self.vector_service.get_stats()

        from ..db.database import NodeDB, EdgeDB
        session = self.db.get_session()
        try:
            conv_count = session.query(ConversationDB).count()
            msg_count = session.query(MessageDB).count()
            node_count = session.query(NodeDB).count()
            edge_count = session.query(EdgeDB).count()
        finally:
            session.close()

        return {
            "active_conversations": conv_count,
            "total_messages": msg_count,
            "graph_nodes": node_count,
            "graph_edges": edge_count,
            "vector_db": vector_stats,
            "config": {
                "user": self.config.user_name,
                "model": self.config.default_model,
            },
        }

    # ==================== PRIVATE ====================

    def _determine_mode(self, message: str) -> ChatMode:
        message_lower = message.lower()

        coaching_keywords = [
            "help me think", "struggle", "challenge", "decision", "should i",
            "advice", "coach", "grow", "develop", "feedback", "improve"
        ]
        query_keywords = [
            "what did", "when did", "have i", "did i", "my decision",
            "tell me about", "find", "show me", "search"
        ]

        coaching_score = sum(1 for k in coaching_keywords if k in message_lower)
        query_score = sum(1 for k in query_keywords if k in message_lower)

        if coaching_score > query_score:
            return ChatMode.COACHING
        elif query_score > 0:
            return ChatMode.QUERY
        else:
            return ChatMode.GENERAL

    _TEMPORAL_KEYWORDS = {
        "last", "latest", "recent", "recently", "newest", "new", "today",
        "yesterday", "added", "first", "oldest", "when", "timeline", "history",
    }

    def _is_temporal_query(self, query: str) -> bool:
        words = set(re.sub(r'[^\w\s]', '', query.lower()).split())
        return len(words & self._TEMPORAL_KEYWORDS) >= 1

    def _get_recent_nodes(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch nodes sorted by created_at descending from SQLite."""
        session = self.db.get_session()
        try:
            nodes = (
                session.query(NodeDB)
                .filter_by(status="done")
                .order_by(NodeDB.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": n.id,
                    "content": f"{n.title or ''}\n{n.summary or ''}\n{(n.content or '')[:1500]}",
                    "metadata": {
                        "title": n.title or "",
                        "type": n.type,
                        "url": n.url or "",
                        "tags": ", ".join(n.tags or []),
                        "created_at": n.created_at.isoformat() if n.created_at else "",
                        "category": (n.node_meta or {}).get("category", ""),
                        "purpose": (n.node_meta or {}).get("purpose", ""),
                    },
                    "relevance_score": 1.0,
                }
                for n in nodes
            ]
        finally:
            session.close()

    def _get_all_canvas_nodes(self) -> list:
        """Fetch compact inventory of ALL canvas nodes for real-time awareness."""
        session = self.db.get_session()
        try:
            nodes = (
                session.query(NodeDB)
                .order_by(NodeDB.created_at.desc())
                .all()
            )
            result = []
            for n in nodes:
                result.append({
                    "id": n.id,
                    "title": n.title or n.url or "Untitled",
                    "url": n.url or "",
                    "type": n.type,
                    "status": n.status,
                    "created_at": n.created_at.isoformat()[:10] if n.created_at else "",
                    "category": (n.node_meta or {}).get("category", ""),
                    "summary": (n.summary or "")[:200],
                })
            return result
        finally:
            session.close()

    def _get_orphan_nodes(self) -> List[Dict[str, str]]:
        """Find nodes with zero edges."""
        session = self.db.get_session()
        try:
            all_nodes = session.query(NodeDB).filter_by(status="done").all()
            edges = session.query(EdgeDB).all()
            connected_ids = set()
            for e in edges:
                connected_ids.add(e.source_id)
                connected_ids.add(e.target_id)
            return [
                {"id": n.id, "title": n.title or "Untitled"}
                for n in all_nodes
                if n.id not in connected_ids
            ]
        finally:
            session.close()

    def _scan_for_people(self, query: str) -> List[Dict[str, Any]]:
        """Scan query for known person names (case-insensitive word-boundary match)."""
        db_session = self.db.get_session()
        try:
            all_people = db_session.query(PersonDB).all()
            if not all_people:
                return []
            query_lower = query.lower()
            matched, seen = [], set()
            for person in all_people:
                pattern = r'\b' + re.escape(person.name_lower) + r'\b'
                if re.search(pattern, query_lower) and person.id not in seen:
                    matched.append(person)
                    seen.add(person.id)
            if not matched:
                return []
            now = datetime.utcnow()
            for p in matched:
                p.last_mentioned_at = now
                p.mention_count = (p.mention_count or 0) + 1
            db_session.commit()
            return [self._person_to_dict(p) for p in matched]
        except Exception as e:
            logger.error("people_scan_error", error=str(e))
            return []
        finally:
            db_session.close()

    def _person_to_dict(self, person: PersonDB) -> Dict[str, Any]:
        return {
            "id": person.id,
            "name": person.name,
            "role": person.role or "",
            "organization": person.organization or "",
            "relationship": person.relationship or "",
            "context": person.context or "",
            "action_items": person.action_items or [],
            "notes": person.notes or [],
            "last_mentioned_at": person.last_mentioned_at.isoformat() if person.last_mentioned_at else "",
        }

    def _retrieve_knowledge(self, query: str) -> Dict[str, Any]:
        try:
            results = self.vector_service.search_all(
                query=query,
                context_limit=self.config.context_top_k,
                document_limit=self.config.search_top_k,
                node_limit=10
            )

            relevant_contexts = [
                ctx for ctx in results.get("contexts", [])
                if ctx.get("relevance_score", 0) > self.config.min_relevance_score
            ]
            relevant_documents = [
                doc for doc in results.get("documents", [])
                if doc.get("relevance_score", 0) > self.config.min_relevance_score
            ]
            # Always include top nodes — canvas content is always relevant context.
            # Vector search already ranks by similarity so top results are most relevant.
            # For temporal queries, also include recent nodes sorted by date.
            relevant_nodes = results.get("nodes", [])

            if self._is_temporal_query(query):
                recent_nodes = self._get_recent_nodes(limit=10)
                # Merge: add recent nodes not already in results
                existing_ids = {n["id"] for n in relevant_nodes}
                for rn in recent_nodes:
                    if rn["id"] not in existing_ids:
                        relevant_nodes.append(rn)
                        existing_ids.add(rn["id"])
            node_ids_missing_date = [
                n["id"] for n in relevant_nodes
                if not n["metadata"].get("created_at")
            ]
            if node_ids_missing_date:
                session = self.db.get_session()
                try:
                    rows = session.query(NodeDB).filter(NodeDB.id.in_(node_ids_missing_date)).all()
                    date_map = {r.id: r.created_at.isoformat() if r.created_at else "" for r in rows}
                    for n in relevant_nodes:
                        if not n["metadata"].get("created_at") and n["id"] in date_map:
                            n["metadata"]["created_at"] = date_map[n["id"]]
                finally:
                    session.close()

            sources = []
            for ctx in relevant_contexts:
                sources.append({
                    "type": "context",
                    "id": ctx["id"],
                    "title": ctx["metadata"].get("title", "Untitled Context"),
                    "context_type": ctx["metadata"].get("type", "unknown"),
                    "date": ctx["metadata"].get("created_at", None),
                    "relevance": round(ctx["relevance_score"], 2),
                })
            for doc in relevant_documents:
                sources.append({
                    "type": "document",
                    "id": doc["metadata"].get("document_id", "unknown"),
                    "filename": doc["metadata"].get("filename", "Unknown Document"),
                    "page": doc["metadata"].get("page_number", None),
                    "relevance": round(doc["relevance_score"], 2),
                })
            for node in relevant_nodes:
                sources.append({
                    "type": "node",
                    "id": node["id"],
                    "title": node["metadata"].get("title", "Untitled"),
                    "node_type": node["metadata"].get("type", "unknown"),
                    "url": node["metadata"].get("url", ""),
                    "created_at": node["metadata"].get("created_at", ""),
                    "relevance": round(node["relevance_score"], 2),
                })

            # Fetch graph edges connecting retrieved nodes and their neighbors
            edges = []
            if self.graph_service and relevant_nodes:
                node_ids = [n["id"] for n in relevant_nodes]
                edges = self.graph_service.get_edges_for_nodes(node_ids)

            people = self._scan_for_people(query)

            return {
                "contexts": relevant_contexts,
                "documents": relevant_documents,
                "nodes": relevant_nodes,
                "edges": edges,
                "sources": sources,
                "people": people,
            }
        except Exception as e:
            logger.error("knowledge_retrieval_error", error=str(e))
            return {"contexts": [], "documents": [], "edges": [], "sources": [], "people": []}

    def _build_prompt(self, mode: ChatMode, knowledge: Dict[str, Any]) -> str:
        base_prompt = self.config.get_system_prompt()
        knowledge_section = []

        # People context injected first so Claude sees it before all other knowledge
        people = knowledge.get("people", [])
        if people:
            knowledge_section.append("\n## People Context\n")
            for person in people:
                header = f"### {person['name']}"
                if person.get("role"):         header += f" — {person['role']}"
                if person.get("organization"): header += f" at {person['organization']}"
                knowledge_section.append(header)
                if person.get("relationship"): knowledge_section.append(f"- **Relationship:** {person['relationship']}")
                if person.get("context"):      knowledge_section.append(f"- **Context:** {person['context']}")
                items = person.get("action_items") or []
                if items:
                    knowledge_section.append("- **Action Items:** " + " | ".join(items))
                for note in (person.get("notes") or [])[-3:]:
                    txt = note.get("text", "") if isinstance(note, dict) else str(note)
                    dat = (note.get("date", "")[:10] if isinstance(note, dict) else "")
                    knowledge_section.append(f"- *Note{' ' + dat if dat else ''}:* {txt}")
                knowledge_section.append("")

        canvas_nodes = knowledge.get("canvas_nodes", [])
        if canvas_nodes:
            total = len(canvas_nodes)
            done_count = sum(1 for n in canvas_nodes if n["status"] == "done")
            knowledge_section.append(f"\n## Canvas ({total} nodes, {done_count} processed)\n")
            for n in canvas_nodes:
                status_flag = "" if n["status"] == "done" else f" [{n['status']}]"
                cat_str = f" — {n['category']}" if n["category"] else ""
                date_str = f" · added {n['created_at']}" if n["created_at"] else ""
                title_line = f"- **{n['title']}**{status_flag} ({n['type']}{cat_str}{date_str})"
                if n["url"]:
                    title_line += f" — {n['url']}"
                knowledge_section.append(title_line)
                if n["status"] == "done" and n["summary"]:
                    knowledge_section.append(f"  ↳ {n['summary']}")
            knowledge_section.append("")

        if knowledge.get("contexts"):
            knowledge_section.append("\n## Relevant Contexts from Your Knowledge Base\n")
            for i, ctx in enumerate(knowledge["contexts"][:5], 1):
                title = ctx["metadata"].get("title", "Untitled")
                ctx_type = ctx["metadata"].get("type", "unknown")
                created = ctx["metadata"].get("created_at", "unknown date")
                knowledge_section.append(f"{i}. **{title}** ({ctx_type}) - {created}")
                knowledge_section.append(f"   {ctx['content']}\n")

        if knowledge.get("documents"):
            knowledge_section.append("\n## Relevant Document Excerpts\n")
            for i, doc in enumerate(knowledge["documents"][:3], 1):
                filename = doc["metadata"].get("filename", "Unknown")
                page = doc["metadata"].get("page_number", "?")
                knowledge_section.append(f"{i}. **{filename}** (page {page})")
                knowledge_section.append(f"   {doc['content'][:500]}...\n")

        if knowledge.get("nodes"):
            knowledge_section.append("\n## Relevant Knowledge Graph Nodes\n")
            for i, node in enumerate(knowledge["nodes"][:5], 1):
                title = node["metadata"].get("title", "Untitled")
                ntype = node["metadata"].get("type", "unknown")
                category = node["metadata"].get("category", "")
                purpose = node["metadata"].get("purpose", "")
                created = node["metadata"].get("created_at", "")
                cat_str = f" - {category}" if category else ""
                date_str = f" - added {created[:10]}" if created else ""
                knowledge_section.append(f"{i}. **{title}** ({ntype}{cat_str}{date_str})")
                if purpose:
                    knowledge_section.append(f"   Purpose: {purpose}")
                knowledge_section.append(f"   {node['content'][:1500]}\n")

        recent_nodes = knowledge.get("recent_nodes", [])
        if recent_nodes:
            knowledge_section.append("\n## All Canvas Nodes (sorted by most recently added)\n")
            for i, node in enumerate(recent_nodes, 1):
                title = node["metadata"].get("title", "Untitled")
                ntype = node["metadata"].get("type", "unknown")
                category = node["metadata"].get("category", "")
                created = node["metadata"].get("created_at", "")
                cat_str = f" - {category}" if category else ""
                date_str = f" - added {created[:10]}" if created else ""
                knowledge_section.append(f"{i}. **{title}** ({ntype}{cat_str}{date_str})")

        orphans = knowledge.get("orphan_nodes", [])
        if orphans:
            knowledge_section.append("\n## Unconnected Nodes (no edges)\n")
            for o in orphans[:5]:
                knowledge_section.append(f"- **{o['title']}** — has no connections yet")
            knowledge_section.append("")

        edges = knowledge.get("edges", [])
        if edges:
            knowledge_section.append("\n## Connections Between Nodes\n")
            for e in edges:
                strength_pct = f" ({e['strength']:.0%})" if e.get("strength") else ""
                knowledge_section.append(
                    f"- **{e['source_title']}** --[{e['label']}]--> **{e['target_title']}**{strength_pct}"
                )
            knowledge_section.append("")

        mode_guidance = ""
        if mode == ChatMode.COACHING:
            mode_guidance = self.config.get_coaching_prompt_addition()
        elif mode == ChatMode.QUERY:
            mode_guidance = """
## Query Mode

The user is looking for specific information from their knowledge base.
- Answer directly based on the provided contexts and documents
- Cite specific sources
- If information is not in the knowledge base, say so clearly
- Be concise and factual
"""

        return "\n".join([
            base_prompt,
            "".join(knowledge_section) if knowledge_section else "",
            mode_guidance,
        ])

    # ==================== PEOPLE TOOLS ====================

    _PEOPLE_TOOLS = [
        {
            "name": "record_person",
            "description": (
                "Record or update a person mentioned in the conversation. "
                "Call this whenever the user mentions a person by name and provides "
                "any context (role, relationship, action items). Also call when new "
                "context about an already-known person emerges. Do NOT call for public "
                "figures unless the user has a personal working relationship with them."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name":         {"type": "string"},
                    "role":         {"type": "string"},
                    "organization": {"type": "string"},
                    "relationship": {"type": "string", "description": "e.g. 'direct report', 'investor', 'peer'"},
                    "context":      {"type": "string", "description": "Concise context paragraph"},
                    "action_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Full current list of action items for this person"
                    },
                    "note": {"type": "string", "description": "Single note to append from this conversation"}
                },
                "required": ["name"]
            }
        }
    ]

    # ==================== SEARCH TOOLS ====================

    _SEARCH_TOOLS = [
        {
            "name": "search_web",
            "description": (
                "Search the internet for current information, recent news, or facts "
                "not present in the user's knowledge base. Use this when the user "
                "explicitly asks to search the web, needs up-to-date information, or "
                "asks about something external that isn't in their canvas or documents. "
                "Do NOT use for questions that can be answered from the canvas."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concise search query"
                    }
                },
                "required": ["query"]
            }
        }
    ]

    # ==================== LINKING INTENT ====================

    _LINKING_KEYWORDS = {
        "link", "connect", "relate", "associate", "tie", "attach",
        "unlink", "disconnect", "detach", "remove", "delete",
    }

    def _has_linking_intent(self, message: str) -> bool:
        words = set(message.lower().split())
        return bool(words & self._LINKING_KEYWORDS)

    # ==================== CANVAS TOOLS ====================

    _CANVAS_TOOLS = [
        {
            "name": "search_canvas_nodes",
            "description": (
                "Search the canvas for nodes (YouTube videos, articles, web pages) "
                "by topic, title, or content. Returns matching node IDs and titles."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find matching canvas nodes"
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "create_canvas_edge",
            "description": (
                "Create a labelled connection (edge) between two canvas nodes. "
                "Use this after finding the node IDs with search_canvas_nodes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": "ID of the source node"
                    },
                    "target_id": {
                        "type": "string",
                        "description": "ID of the target node"
                    },
                    "label": {
                        "type": "string",
                        "description": "Short label describing the relationship (e.g. 'related to', 'builds on')"
                    }
                },
                "required": ["source_id", "target_id", "label"]
            }
        },
        {
            "name": "delete_canvas_edge",
            "description": (
                "Delete a connection (edge) between two canvas nodes. "
                "Use search_canvas_nodes first to find the node IDs, then delete the edge between them."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": "ID of one of the connected nodes"
                    },
                    "target_id": {
                        "type": "string",
                        "description": "ID of the other connected node"
                    }
                },
                "required": ["source_id", "target_id"]
            }
        }
    ]

    def _execute_tool(self, name: str, inputs: Dict[str, Any], actions: List[Dict]) -> Any:
        """Execute a tool call and return the result."""

        if name == "record_person":
            person_name = (inputs.get("name") or "").strip()
            if not person_name:
                return {"error": "name is required"}
            db_session = self.db.get_session()
            try:
                name_lower = person_name.lower()
                existing = db_session.query(PersonDB).filter(
                    PersonDB.name_lower == name_lower
                ).first()
                now = datetime.utcnow()
                if existing:
                    if inputs.get("role"):         existing.role = inputs["role"]
                    if inputs.get("organization"): existing.organization = inputs["organization"]
                    if inputs.get("relationship"): existing.relationship = inputs["relationship"]
                    if inputs.get("context"):      existing.context = inputs["context"]
                    if inputs.get("action_items") is not None:
                        existing.action_items = inputs["action_items"]
                    if inputs.get("note"):
                        notes = list(existing.notes or [])
                        notes.append({"text": inputs["note"], "date": now.isoformat()})
                        existing.notes = notes
                    existing.last_mentioned_at = now
                    existing.mention_count = (existing.mention_count or 0) + 1
                    db_session.commit()
                    actions.append({"type": "person_updated", "name": existing.name})
                    logger.info("person_updated", name=existing.name)
                    return {"success": True, "action": "updated", "name": existing.name}
                else:
                    notes = [{"text": inputs["note"], "date": now.isoformat()}] if inputs.get("note") else []
                    p = PersonDB(
                        id=str(uuid.uuid4()), name=person_name, name_lower=name_lower,
                        role=inputs.get("role"), organization=inputs.get("organization"),
                        relationship=inputs.get("relationship"), context=inputs.get("context"),
                        action_items=inputs.get("action_items") or [], notes=notes,
                        first_mentioned_at=now, last_mentioned_at=now, mention_count=1,
                    )
                    db_session.add(p)
                    db_session.commit()
                    actions.append({"type": "person_created", "name": person_name})
                    logger.info("person_created", name=person_name)
                    return {"success": True, "action": "created", "name": person_name}
            except Exception as e:
                logger.error("record_person_error", error=str(e))
                return {"error": str(e)}
            finally:
                db_session.close()

        if name == "search_canvas_nodes":
            query = inputs.get("query", "")
            nodes = self.vector_service.search_nodes(query, limit=5)
            return [
                {
                    "id": n["id"],
                    "title": n["metadata"].get("title", "Untitled"),
                    "type": n["metadata"].get("type", "unknown"),
                    "url": n["metadata"].get("url", ""),
                    "relevance": round(n.get("relevance_score", 0), 2)
                }
                for n in nodes
            ]

        if name == "create_canvas_edge":
            source_id = inputs.get("source_id", "")
            target_id = inputs.get("target_id", "")
            label = inputs.get("label", "related to")

            db_session = self.db.get_session()
            try:
                source = db_session.query(NodeDB).filter_by(id=source_id).first()
                target = db_session.query(NodeDB).filter_by(id=target_id).first()
                if not source or not target:
                    return {"error": "One or both node IDs not found in canvas"}

                edge_id = str(uuid.uuid4())
                edge = EdgeDB(
                    id=edge_id,
                    source_id=source_id,
                    target_id=target_id,
                    label=label,
                    created_at=datetime.utcnow()
                )
                db_session.add(edge)
                db_session.commit()

                actions.append({
                    "type": "edge_created",
                    "edge_id": edge_id,
                    "source_id": source_id,
                    "target_id": target_id,
                    "source_title": source.title or "Untitled",
                    "target_title": target.title or "Untitled",
                    "label": label
                })

                logger.info("canvas_edge_created", edge_id=edge_id, source=source_id, target=target_id)
                return {
                    "success": True,
                    "edge_id": edge_id,
                    "source_title": source.title or "Untitled",
                    "target_title": target.title or "Untitled"
                }
            finally:
                db_session.close()

        if name == "delete_canvas_edge":
            source_id = inputs.get("source_id", "")
            target_id = inputs.get("target_id", "")

            db_session = self.db.get_session()
            try:
                edge = db_session.query(EdgeDB).filter(
                    ((EdgeDB.source_id == source_id) & (EdgeDB.target_id == target_id)) |
                    ((EdgeDB.source_id == target_id) & (EdgeDB.target_id == source_id))
                ).first()

                if not edge:
                    return {"error": "No edge found between these two nodes"}

                edge_id = edge.id
                source_node = db_session.query(NodeDB).filter_by(id=source_id).first()
                target_node = db_session.query(NodeDB).filter_by(id=target_id).first()
                source_title = (source_node.title or "Untitled") if source_node else "Unknown"
                target_title = (target_node.title or "Untitled") if target_node else "Unknown"

                db_session.delete(edge)
                db_session.commit()

                actions.append({
                    "type": "edge_removed",
                    "edge_id": edge_id,
                    "source_id": source_id,
                    "target_id": target_id,
                    "source_title": source_title,
                    "target_title": target_title,
                })

                logger.info("canvas_edge_deleted", edge_id=edge_id, source=source_id, target=target_id)
                return {
                    "success": True,
                    "edge_id": edge_id,
                    "source_title": source_title,
                    "target_title": target_title,
                }
            finally:
                db_session.close()

        if name == "search_web":
            query = inputs.get("query", "")
            if not query:
                return {"error": "query is required"}
            result = self._execute_search_web(query)
            actions.append({"type": "web_search", "query": query})
            return result

        return {"error": f"Unknown tool: {name}"}

    def _execute_search_web(self, query: str) -> Dict[str, Any]:
        """Search the web using Haiku + Anthropic's built-in web search tool."""
        try:
            logger.info("web_search", query=query)
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                messages=[{
                    "role": "user",
                    "content": (
                        f"Search for: {query}\n\n"
                        "Provide a concise, factual summary of the most relevant "
                        "results. Include key facts, dates, and source URLs."
                    )
                }]
            )
            text_blocks = [b.text for b in response.content if hasattr(b, "text")]
            summary = " ".join(text_blocks).strip() if text_blocks else "No results found."
            return {"query": query, "result": summary}
        except Exception as e:
            logger.error("web_search_error", query=query, error=str(e))
            return {"query": query, "error": f"Search failed: {str(e)}"}

    # ==================== CLAUDE API ====================

    def _call_claude_with_retry(self, system_prompt, messages, max_retries=3, **kwargs):
        """Call Claude API with retry on 529 overloaded errors."""
        for attempt in range(max_retries):
            try:
                return self.client.messages.create(
                    model=self.config.default_model,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system=system_prompt,
                    messages=messages,
                    **kwargs
                )
            except APIStatusError as e:
                if e.status_code == 529 and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    logger.warning("claude_overloaded_retrying", attempt=attempt + 1, wait=wait)
                    time.sleep(wait)
                else:
                    raise

    # ==================== GENERATE RESPONSE ====================

    def _generate_response(
        self,
        system_prompt: str,
        conversation: List[Dict[str, Any]],
        enable_canvas_tools: bool = False
    ) -> Tuple[str, List[Dict]]:
        messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in conversation
            if msg["role"] in ["user", "assistant"]
        ]

        actions: List[Dict] = []

        # People + search always enabled; canvas tools only when linking intent detected
        tools = list(self._PEOPLE_TOOLS) + list(self._SEARCH_TOOLS)
        if enable_canvas_tools:
            tools.extend(self._CANVAS_TOOLS)

        try:
            # Agentic tool_use loop (max 5 iterations)
            tool_messages = list(messages)
            for _ in range(5):
                response = self._call_claude_with_retry(
                    system_prompt, tool_messages,
                    tools=tools,
                    tool_choice={"type": "auto"}
                )

                if response.stop_reason != "tool_use":
                    # Extract text from response content
                    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
                    return " ".join(text_blocks) if text_blocks else "", actions

                # Append assistant turn (with tool_use blocks)
                tool_messages.append({"role": "assistant", "content": response.content})

                # Execute each tool call and collect results
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._execute_tool(block.name, block.input, actions)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result)
                        })

                tool_messages.append({"role": "user", "content": tool_results})

            # Fallback if loop exhausted
            return "I wasn't able to complete that action. Please try again.", actions

        except Exception as e:
            logger.error("claude_api_error", error=str(e))
            return (
                "I apologize, but I'm having a technical issue at the moment. "
                "Could you please try rephrasing your question?",
                actions
            )
