"""Document data models for Tacit"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field
from enum import Enum


class DocumentType(str, Enum):
    """Supported document types"""
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MD = "md"
    PPTX = "pptx"


class Document(BaseModel):
    """A document uploaded to the knowledge base"""
    id: Optional[str] = None
    filename: str
    original_filename: str
    type: DocumentType
    size_bytes: int
    page_count: Optional[int] = None
    word_count: Optional[int] = None
    upload_date: datetime = Field(default_factory=datetime.utcnow)
    tags: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    metadata: dict = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    """A chunk of text from a document"""
    document_id: str
    chunk_id: str
    content: str
    page_number: Optional[int] = None
    chunk_index: int
    metadata: dict = Field(default_factory=dict)


class DocumentSearchQuery(BaseModel):
    """Search query for documents"""
    query: str
    document_types: Optional[List[DocumentType]] = None
    tags: Optional[List[str]] = None
    limit: int = Field(default=5, ge=1, le=20)


class DocumentSearchResult(BaseModel):
    """A search result from document query"""
    document_id: str
    filename: str
    chunk_content: str
    page_number: Optional[int] = None
    relevance_score: float
    metadata: dict = Field(default_factory=dict)
