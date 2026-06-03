"""SQLite database setup and models"""

import os
import time
import structlog
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

logger = structlog.get_logger()


# DATA_DIR env var allows mounting a persistent disk on Render/cloud.
# Falls back to ~/.tacit/data for local dev (outside iCloud).
import os as _os
DEFAULT_DATA_DIR = Path(_os.getenv("DATA_DIR", str(Path.home() / ".tacit" / "data")))
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "tacit.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DB_PATH}"

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


class UserDB(Base):
    """User account synced from Clerk"""
    __tablename__ = "users"

    id         = Column(String, primary_key=True)   # Clerk user ID
    email      = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class UserUsageDB(Base):
    """Token usage tracking for billing"""
    __tablename__ = "user_usage"

    user_id                = Column(String, primary_key=True)
    plan                   = Column(String(20), default="free")   # "free" | "pro"
    tokens_used            = Column(Integer, default=0)
    period_start           = Column(DateTime, default=datetime.utcnow)
    stripe_customer_id     = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    updated_at             = Column(DateTime, default=datetime.utcnow)


class NodeDB(Base):
    """Canvas node - any piece of ingested content"""
    __tablename__ = "nodes"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=True)
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
    user_id = Column(String, index=True, nullable=True)
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
    user_id            = Column(String, index=True, nullable=True)
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


class UserSettingsDB(Base):
    """Single-row settings table for user personalization"""
    __tablename__ = "user_settings"

    id           = Column(String, primary_key=True, default="default")
    user_name    = Column(String(200), default="User")
    user_role    = Column(String(200), default="")
    organization = Column(String(200), default="")
    updated_at   = Column(DateTime, default=datetime.utcnow)


class ShareTokenDB(Base):
    """Share token for read-only canvas access"""
    __tablename__ = "share_tokens"

    token = Column(String, primary_key=True)
    label = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    revoked = Column(Integer, default=0)


class Database:
    """Database manager"""

    def __init__(self, database_url: str = DEFAULT_DATABASE_URL):
        self.database_url = database_url
        self._build_engine()
        Base.metadata.create_all(bind=self.engine)
        logger.info("database_initialized", database_url=database_url)

    def _build_engine(self):
        # Ensure parent dir exists for sqlite file URLs
        if self.database_url.startswith("sqlite:///"):
            db_file = self.database_url.replace("sqlite:///", "", 1)
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)

        is_sqlite = "sqlite" in self.database_url
        self.engine = create_engine(
            self.database_url,
            connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {},
            poolclass=NullPool if is_sqlite else None,
        )

        if is_sqlite:
            @event.listens_for(self.engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA temp_store=MEMORY")
                cursor.close()

        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def _recycle_engine(self):
        """Tear down and rebuild the engine — used when the connection pool is wedged."""
        try:
            self.engine.dispose()
        except Exception:
            pass
        self._build_engine()
        logger.warning("database_engine_recycled")

    def get_session(self):
        return self.SessionLocal()

    @contextmanager
    def session_scope(self):
        """Yield a session that auto-commits on success, rolls back on error, always closes.

        On a SQLite 'disk I/O error' / 'database is locked', recycle the engine then re-raise
        so the next call gets a fresh connection pool. (Cannot retry from inside a
        contextmanager — see run_with_retry for the wrapper that retries the operation.)
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except OperationalError as e:
            try:
                session.rollback()
            except Exception:
                pass
            msg = str(e).lower()
            if "disk i/o error" in msg or "database is locked" in msg or "database disk image is malformed" in msg:
                logger.warning("sqlite_transient_error_recycling_engine", error=str(e))
                try:
                    session.close()
                except Exception:
                    pass
                self._recycle_engine()
            raise
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                session.close()
            except Exception:
                pass

    def run_with_retry(self, fn, max_attempts: int = 2, backoff: float = 0.1):
        """Run fn(session) inside a session_scope with retry on transient SQLite errors.

        fn receives a fresh Session and must do its work + return a result. Auto-commit
        happens via session_scope. On 'disk I/O error' / 'database is locked', the engine
        is recycled and fn is called again with a brand new session.
        """
        last_exc = None
        for attempt in range(max_attempts):
            try:
                with self.session_scope() as session:
                    return fn(session)
            except OperationalError as e:
                last_exc = e
                msg = str(e).lower()
                transient = (
                    "disk i/o error" in msg
                    or "database is locked" in msg
                    or "database disk image is malformed" in msg
                )
                if transient and attempt < max_attempts - 1:
                    time.sleep(backoff)
                    continue
                raise
        if last_exc:
            raise last_exc

    def close(self):
        self.engine.dispose()


# Singleton instance
_db_instance = None


def get_database(database_url: str = None) -> Database:
    global _db_instance
    if _db_instance is None:
        url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        _db_instance = Database(url)
    return _db_instance
