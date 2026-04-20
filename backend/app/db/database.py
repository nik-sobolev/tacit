"""SQLite database setup and models"""

import structlog
from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os

logger = structlog.get_logger()

Base = declarative_base()


class ContextDB(Base):
    """Context database model"""
    __tablename__ = "contexts"

    id = Column(String, primary_key=True)
    title = Column(String(200), nullable=False)
    type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(JSON, default=list)
    related_to = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True)
    extra_metadata = Column(JSON, default=dict)


class DocumentDB(Base):
    """Document database model"""
    __tablename__ = "documents"

    id = Column(String, primary_key=True)
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    type = Column(String(10), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    page_count = Column(Integer, nullable=True)
    word_count = Column(Integer, nullable=True)
    upload_date = Column(DateTime, default=datetime.utcnow)
    tags = Column(JSON, default=list)
    description = Column(Text, nullable=True)
    extra_metadata = Column(JSON, default=dict)


class NodeDB(Base):
    """Canvas node - any piece of ingested content"""
    __tablename__ = "nodes"

    id = Column(String, primary_key=True)
    type = Column(String(30), nullable=False)       # youtube|webpage|tiktok|instagram|note|document|text
    title = Column(String(500), nullable=True)
    content = Column(Text, nullable=True)           # full transcript or page text
    summary = Column(Text, nullable=True)           # AI-generated summary
    url = Column(String(2000), nullable=True)
    thumbnail_url = Column(String(2000), nullable=True)
    canvas_x = Column(Float, default=100.0)
    canvas_y = Column(Float, default=100.0)
    status = Column(String(20), default="pending")  # pending|processing|done|error
    error_message = Column(Text, nullable=True)
    tags = Column(JSON, default=list)
    node_meta = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)


class EdgeDB(Base):
    """Relationship between two canvas nodes"""
    __tablename__ = "edges"

    id = Column(String, primary_key=True)
    source_id = Column(String, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    target_id = Column(String, ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False)
    relationship_type = Column(String(30), default="semantic")  # semantic|topic|temporal|manual
    strength = Column(Float, default=0.5)
    label = Column(String(200), nullable=True)
    auto_generated = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ConversationDB(Base):
    """Conversation session database model"""
    __tablename__ = "conversations"

    id = Column(String, primary_key=True)
    title = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)
    message_count = Column(Integer, default=0)


class MessageDB(Base):
    """Chat message database model"""
    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    mode = Column(String(20), nullable=True)
    sources = Column(JSON, default=list)


class PersonDB(Base):
    """A person remembered from conversations"""
    __tablename__ = "people"

    id                 = Column(String, primary_key=True)
    name               = Column(String(200), nullable=False)
    name_lower         = Column(String(200), nullable=False)
    role               = Column(String(200), nullable=True)
    organization       = Column(String(200), nullable=True)
    relationship       = Column(String(200), nullable=True)
    context            = Column(Text, nullable=True)
    action_items       = Column(JSON, default=list)
    notes              = Column(JSON, default=list)
    first_mentioned_at = Column(DateTime, default=datetime.utcnow)
    last_mentioned_at  = Column(DateTime, default=datetime.utcnow)
    mention_count      = Column(Integer, default=1)


class Database:
    """Database manager"""

    def __init__(self, database_url: str = "sqlite:///./data/tacit.db"):
        """Initialize database"""

        # Ensure data directory exists
        os.makedirs("./data", exist_ok=True)

        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if "sqlite" in database_url else {},
            pool_pre_ping=True,
        )

        # Enable WAL mode for better concurrent read/write handling
        if "sqlite" in database_url:
            from sqlalchemy import event, text
            @event.listens_for(self.engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.close()

        # Create tables
        Base.metadata.create_all(bind=self.engine)

        # Create session factory
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        logger.info("database_initialized", database_url=database_url)

    def get_session(self):
        """Get a database session"""
        return self.SessionLocal()

    def close(self):
        """Close database connection"""
        self.engine.dispose()


# Singleton instance
_db_instance = None


def get_database(database_url: str = "sqlite:///./data/tacit.db") -> Database:
    """Get or create database instance"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(database_url)
    return _db_instance
