"""Chat API endpoints"""

from fastapi import APIRouter, HTTPException, Request
from typing import Optional
import uuid

from ..models.chat import ChatRequest, ChatResponse, ChatMode

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def send_message(request: Request, chat_request: ChatRequest):
    """Send a message to the twin and get a response"""
    try:
        engine = request.app.state.engine

        # Generate or use provided session ID
        session_id = chat_request.session_id or str(uuid.uuid4())

        # Process message through engine
        result = engine.process_message(
            session_id=session_id,
            user_message=chat_request.message,
            mode=chat_request.mode
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
async def list_conversations(request: Request, limit: int = 50):
    """List all conversations ordered by most recent activity"""
    from ..db.database import get_database, ConversationDB, MessageDB
    from sqlalchemy import desc
    db = get_database()
    session = db.get_session()
    try:
        convs = (
            session.query(ConversationDB)
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
