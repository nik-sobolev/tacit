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
            metadata=result.get('metadata', {})
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
