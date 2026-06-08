"""Chat API endpoints"""

import asyncio
import threading
import uuid as _uuid
from datetime import datetime as _datetime
from fastapi import APIRouter, HTTPException, Request, Depends
from typing import Optional
import uuid

from ..models.chat import ChatRequest, ChatResponse, ChatMode
from ..core.auth import get_current_user
from ..db.database import get_database, UserDB, UserSettingsDB, NodeDB
from ..core.config import TacitConfig

router = APIRouter()

WELCOME_TEXT = """Tacit is your personal second brain. Here's how to get started:

• Drop any URL in the bar above — YouTube, articles, TikToks, webpages
• Tacit extracts the content, summarizes it, and places it on your canvas
• Ask me anything in the chat about what you've saved
• Paste text in chat to save notes: "save this: [your text]"
• Click any card to see full content and related nodes

The canvas grows as you add content. Start by adding something you read this week."""

STARTER_URLS = [
    "https://fs.blog/feynman-technique/",
    "https://paulgraham.com/read.html",
]


def _seed_starter_content(user_id: str, graph_service):
    def _seed():
        try:
            db = get_database()
            # Welcome note
            note_id = str(_uuid.uuid4())
            with db.session_scope() as s:
                s.add(NodeDB(
                    id=note_id, user_id=user_id, type="note",
                    title="Welcome to Tacit", content=WELCOME_TEXT,
                    status="processing", canvas_x=100, canvas_y=120,
                    tags=[], node_meta={}, created_at=_datetime.utcnow(),
                ))
            graph_service.process_node(note_id)

            # Starter articles
            from ..services.ingestion_service import IngestionService
            svc = IngestionService()
            for i, url in enumerate(STARTER_URLS):
                try:
                    node = svc.ingest_url(url, canvas_x=520 + i * 340, canvas_y=120)
                    with db.session_scope() as s:
                        n = s.query(NodeDB).filter_by(id=node.id).first()
                        if n:
                            n.user_id = user_id
                    graph_service.process_node(node.id)
                except Exception:
                    pass
        except Exception:
            pass

    threading.Thread(target=_seed, daemon=True).start()


def _upsert_user(user: dict, graph_service=None):
    """Create user record on first login; seed content if canvas is empty."""
    db = get_database()
    try:
        with db.session_scope() as s:
            if not s.query(UserDB).filter_by(id=user["id"]).first():
                s.add(UserDB(id=user["id"], email=user.get("email", "")))
            # Seed for any user with 0 nodes (new or existing with empty canvas)
            if graph_service:
                node_count = s.query(NodeDB).filter_by(user_id=user["id"]).count()
                if node_count == 0:
                    _seed_starter_content(user["id"], graph_service)
    except Exception:
        pass


def _load_user_config(user_id: str, base_config: TacitConfig) -> TacitConfig:
    """Load per-user settings and return a personalized config."""
    db = get_database()
    try:
        with db.session_scope() as s:
            row = s.query(UserSettingsDB).filter_by(id=user_id).first()
            if row:
                cfg = base_config.model_copy()
                cfg.user_name = row.user_name or base_config.user_name
                cfg.user_role = row.user_role or base_config.user_role
                cfg.user_organization = row.organization or base_config.user_organization
                return cfg
    except Exception:
        pass
    return base_config


@router.post("/chat", response_model=ChatResponse)
async def send_message(
    request: Request,
    chat_request: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    """Send a message to the twin and get a response"""
    try:
        _upsert_user(current_user, graph_service=request.app.state.graph_service)
        engine = request.app.state.engine

        # Load per-user config
        user_config = _load_user_config(current_user["id"], request.app.state.config)
        engine.config = user_config

        # Generate or use provided session ID, scoped to user
        session_id = chat_request.session_id or str(uuid.uuid4())

        # Process message through engine
        result = engine.process_message(
            session_id=session_id,
            user_message=chat_request.message,
            mode=chat_request.mode,
            user_id=current_user["id"]
        )

        # Kick off background graph processing for any URLs ingested via chat
        graph_service = request.app.state.graph_service
        for action in result.get('actions', []):
            if action.get('type') == 'ingest_started':
                node_id = action['node_id']
                asyncio.get_event_loop().run_in_executor(
                    None, graph_service.process_node, node_id
                )

        return ChatResponse(
            response=result['response'],
            session_id=session_id,
            mode=result['mode'],
            sources=result.get('sources', []),
            metadata=result.get('metadata', {}),
            actions=result.get('actions', [])
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/history/{session_id}")
async def get_chat_history(request: Request, session_id: str):
    """Get conversation history for a session"""
    try:
        engine = request.app.state.engine
        conversation = engine.get_conversation(session_id)

        return {
            "session_id": session_id,
            "messages": conversation,
            "message_count": len(conversation)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/chat/{session_id}")
async def clear_chat(request: Request, session_id: str):
    """Clear conversation history for a session"""
    try:
        engine = request.app.state.engine
        engine.clear_conversation(session_id)

        return {"success": True, "session_id": session_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/new")
async def new_chat_session():
    """Create a new chat session"""
    session_id = str(uuid.uuid4())

    return {
        "session_id": session_id,
        "created_at": None
    }


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """List conversations for the current user ordered by most recent activity"""
    from ..db.database import get_database, ConversationDB, MessageDB
    from sqlalchemy import desc, or_
    db = get_database()
    session = db.get_session()
    try:
        convs = (
            session.query(ConversationDB)
            .filter(
                or_(
                    ConversationDB.user_id == current_user["id"],
                    ConversationDB.user_id.is_(None),  # legacy rows without user_id
                )
            )
            .order_by(desc(ConversationDB.last_activity))
            .limit(limit)
            .all()
        )
        result = []
        for c in convs:
            # Get the first user message as a preview title
            first_msg = (
                session.query(MessageDB)
                .filter_by(conversation_id=c.id, role="user")
                .order_by(MessageDB.timestamp)
                .first()
            )
            preview = first_msg.content[:60] if first_msg else "Empty conversation"
            result.append({
                "session_id": c.id,
                "title": c.title or preview,
                "preview": preview,
                "message_count": c.message_count or 0,
                "last_activity": c.last_activity.isoformat() if c.last_activity else None,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            })
        return {"conversations": result}
    finally:
        session.close()


@router.get("/people")
async def list_people(request: Request):
    """List all remembered people ordered by most recently mentioned"""
    from ..db.database import get_database, PersonDB
    from sqlalchemy import desc
    db = get_database()
    session = db.get_session()
    try:
        people = (
            session.query(PersonDB)
            .order_by(desc(PersonDB.last_mentioned_at))
            .all()
        )
        result = []
        for p in people:
            result.append({
                "id": p.id,
                "name": p.name,
                "role": p.role,
                "organization": p.organization,
                "relationship": p.relationship,
                "context": p.context,
                "action_items": p.action_items or [],
                "notes": p.notes or [],
                "first_mentioned_at": p.first_mentioned_at.isoformat() if p.first_mentioned_at else None,
                "last_mentioned_at": p.last_mentioned_at.isoformat() if p.last_mentioned_at else None,
                "mention_count": p.mention_count,
            })
        return {"people": result}
    finally:
        session.close()


@router.delete("/people/{person_id}")
async def delete_person(person_id: str):
    """Delete a person from memory"""
    from ..db.database import get_database, PersonDB
    db = get_database()
    session = db.get_session()
    try:
        person = session.query(PersonDB).filter_by(id=person_id).first()
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
        session.delete(person)
        session.commit()
        return {"success": True, "id": person_id}
    finally:
        session.close()
