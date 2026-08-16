"""
core/database.py
Creates the SQLAlchemy engine + session factory used across the app.
Import `get_session()` (context manager) or `SessionLocal` directly.
"""
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,   # avoids "MySQL server has gone away" on idle connections
    pool_recycle=3600,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


@contextmanager
def get_session():
    """Usage: with get_session() as db: db.query(...)"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def test_connection() -> bool:
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


def init_db() -> None:
    """Create any tables that don't exist yet (medicines, prescriptions,
    prescription_items were added after the original MySQL Workbench schema).
    create_all only CREATEs missing tables — it never alters or drops
    existing ones, so it's safe to run on every startup."""
    import models.models  # noqa: F401 — registers all models on Base.metadata
    Base.metadata.create_all(bind=engine)
