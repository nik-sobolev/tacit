"""Chat data models for Tacit"""

from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from enum import Enum


class ChatMode(str, Enum):
    """Chat interaction modes"""
    GENERAL = "general"  # General questions about knowledge
    COACHING = "coaching"  # Executive coaching mode
    QUERY = "query"  # Direct knowledge query
    MIXED = "mixed"  # Combination of modes


class ChatMessage(BaseModel):
    """A single chat message"""
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    mode: Optional[ChatMode] = None
    sources: List[dict] = Field(default_factory=list)  # Citations
    metadata: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """Request to send a chat message"""
    message: str = Field(..., min_length=1)
    session_id: Optional[str] = None
    mode: Optional[ChatMode] = None


class ChatResponse(BaseModel):
    """Response from the twin"""
    response: str
    session_id: str
    mode: ChatMode
    sources: List[dict] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class ChatSession(BaseModel):
    """A chat session with the twin"""
    id: str
    user_id: str
    start_time: datetime = Field(default_factory=datetime.utcnow)
    last_activity: datetime = Field(default_factory=datetime.utcnow)
    messages: List[ChatMessage] = Field(default_factory=list)
    mode: ChatMode = ChatMode.GENERAL
    metadata: dict = Field(default_factory=dict)
