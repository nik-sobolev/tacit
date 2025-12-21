"""Context data models for Tacit"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


class ContextType(str, Enum):
    """Types of context that can be captured"""
    DECISION = "decision"
    MEETING_NOTE = "meeting_note"
    PROJECT_CONTEXT = "project_context"
    STRATEGY = "strategy"
    INSIGHT = "insight"
    PLAN = "plan"


class Context(BaseModel):
    """A piece of tacit knowledge captured by the user"""
    id: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=200)
    type: ContextType
    content: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)
    related_to: Optional[str] = None  # Project, person, or topic
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None
    metadata: dict = Field(default_factory=dict)


class ContextCreate(BaseModel):
    """Request model for creating context"""
    title: str = Field(..., min_length=1, max_length=200)
    type: ContextType
    content: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)
    related_to: Optional[str] = None


class ContextUpdate(BaseModel):
    """Request model for updating context"""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    type: Optional[ContextType] = None
    content: Optional[str] = Field(None, min_length=1)
    tags: Optional[List[str]] = None
    related_to: Optional[str] = None


class ContextSearchQuery(BaseModel):
    """Search query for contexts"""
    query: str
    type: Optional[ContextType] = None
    tags: Optional[List[str]] = None
    limit: int = Field(default=10, ge=1, le=50)
