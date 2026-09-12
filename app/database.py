"""SQLAlchemy engine and session wiring.

Deliberately synchronous: FastAPI runs `def` endpoints in a threadpool, call
volume here is one phone conversation at a time, and sync psycopg2 avoids the
asyncpg + PgBouncer prepared-statement failure modes that eat debugging time.
"""
import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# Supabase's session pooler hands out connections that can be recycled server
# side; pre-ping avoids handing a dead connection to a request mid-call.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables if they do not exist.

    Fine for an assessment; a real deployment would use Alembic migrations.
    """
    from app import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=engine)
    logger.info("Database schema ensured")
