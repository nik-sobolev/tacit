"""SQLite database setup and models"""

import structlog
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, JSON
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


class Database:
    """Database manager"""

    def __init__(self, database_url: str = "sqlite:///./data/tacit.db"):
        """Initialize database"""

        # Ensure data directory exists
        os.makedirs("./data", exist_ok=True)

        self.engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False} if "sqlite" in database_url else {}
        )

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
