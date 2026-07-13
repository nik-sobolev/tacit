"""SQLite database setup and models"""

import os
import time
import structlog
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, Column, String, Integer, Float, Boolean, DateTime, Text, JSON, ForeignKey, event, text, UniqueConstraint
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
    user_id = Column(String, index=True, nullable=True)   # Clerk user ID — nullable for legacy rows
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
    user_id = Column(String, index=True, nullable=True)
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


class UserQuickTokenDB(Base):
    """Long-lived token for iOS Siri Shortcut / mobile quick-add"""
    __tablename__ = "user_quick_tokens"

    token      = Column(String, primary_key=True)   # UUID, never expires
    user_id    = Column(String, index=True, nullable=False)
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


class UsagePeriodDB(Base):
    """Human-unit usage counters for one user, one billing cycle (usage v2).
    Additive alongside UserUsageDB (token/plan/Stripe-ID data) — does not replace it.
    period_start/period_end come from Stripe's current_period_start/end, not calendar
    month, so counters reset on the user's actual billing-cycle boundary."""
    __tablename__ = "usage_periods"
    __table_args__ = (UniqueConstraint("user_id", "period_start", name="uq_usage_period_user_start"),)

    id                    = Column(String, primary_key=True)   # uuid4
    user_id               = Column(String, index=True, nullable=False)
    period_start          = Column(DateTime, nullable=False)
    period_end            = Column(DateTime, nullable=False)
    tier                  = Column(String(20), nullable=False)  # "free" | "core" | "operator" | "superadmin"

    saves_count           = Column(Integer, default=0)
    queries_count         = Column(Integer, default=0)
    synthesis_count       = Column(Integer, default=0)
    digests_count         = Column(Integer, default=0)
    agent_runs_count      = Column(Integer, default=0)

    # Server-side-only cost tracking for margin monitoring — never returned to the frontend.
    estimated_cost_cents  = Column(Integer, default=0)

    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow)


class UsageEventDB(Base):
    """Append-only idempotency ledger for usage v2. One row per successfully-counted
    action. The (user_id, dedupe_key) unique constraint is what makes increments
    idempotent under client retries or double-fires."""
    __tablename__ = "usage_events"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key", name="uq_usage_event_user_dedupe"),)

    id            = Column(String, primary_key=True)   # uuid4
    user_id       = Column(String, index=True, nullable=False)
    category      = Column(String(20), nullable=False)  # "save"|"query"|"synthesis"|"digest"|"agent_run"
    dedupe_key    = Column(String(200), nullable=False)
    period_id     = Column(String, index=True, nullable=False)  # references usage_periods.id (no FK — matches
                                                                  # this codebase's convention of manual filtered
                                                                  # queries instead of ORM relationships)
    tokens_input  = Column(Integer, default=0)   # server-side only, never exposed to the frontend
    tokens_output = Column(Integer, default=0)   # server-side only
    cost_cents    = Column(Integer, default=0)   # server-side only
    created_at    = Column(DateTime, default=datetime.utcnow)


class UsageAuditLogDB(Base):
    """Append-only audit trail for usage v2: tier changes and cap events. Never
    updated or deleted after insert — support/dispute record."""
    __tablename__ = "usage_audit_log"

    id          = Column(String, primary_key=True)  # uuid4
    user_id     = Column(String, index=True, nullable=False)
    event_type  = Column(String(30), nullable=False)  # "tier_changed"|"cap_warned"|"cap_hit"|"tier_reconciled"
    from_value  = Column(String(50), nullable=True)
    to_value    = Column(String(50), nullable=True)
    source      = Column(String(30), nullable=False)   # "stripe_webhook"|"recovery_endpoint"|"enforcement"|"migration"
    detail      = Column(Text, nullable=True)           # short human-readable note — no user content, ever
    created_at  = Column(DateTime, default=datetime.utcnow)


class StripeWebhookEventDB(Base):
    """Records every Stripe webhook event ID successfully processed, so a retried
    delivery (Stripe resends on non-2xx or timeout) is a no-op on replay."""
    __tablename__ = "stripe_webhook_events"

    event_id     = Column(String, primary_key=True)   # Stripe's event.id, globally unique
    event_type   = Column(String(50), nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)


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
    """Per-user settings table for personalization. PK is Clerk user_id."""
    __tablename__ = "user_settings"

    id           = Column(String, primary_key=True)   # Clerk user_id (never "default")
    user_name    = Column(String(200), default="User")
    user_role    = Column(String(200), default="")
    organization = Column(String(200), default="")
    updated_at   = Column(DateTime, default=datetime.utcnow)


class ShareTokenDB(Base):
    """Share token for read-only canvas access"""
    __tablename__ = "share_tokens"

    token = Column(String, primary_key=True)
    user_id = Column(String, index=True, nullable=True)  # Owner of the share token
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


def filter_owned_ids(session, model, ids, user_id: str) -> set:
    """Return the subset of `ids` that `model` rows show as owned by `user_id`.

    Used to re-verify ownership of ChromaDB search hits in SQL (the source of
    truth for user_id) since ChromaDB itself has no per-tenant isolation — every
    caller of vector_service's search methods needs this, so it lives here
    rather than being copy-pasted per call site. A falsy user_id or empty ids
    always yields an empty set (fail closed), never "everything."
    """
    if not user_id or not ids:
        return set()
    return {
        r.id for r in session.query(model.id).filter(model.id.in_(ids), model.user_id == user_id).all()
    }


# Singleton instance
_db_instance = None


def get_database(database_url: str = None) -> Database:
    global _db_instance
    if _db_instance is None:
        url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        _db_instance = Database(url)
    return _db_instance
