"""Database engine, session factory, and the FastAPI session dependency."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

DEFAULT_DATABASE_URL = "sqlite:///./local.db"


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def normalize_database_url(url: str) -> str:
    """Point Postgres URLs at the psycopg 3 driver; fall back to local SQLite when unset.

    Neon and most hosts provide `postgresql://...` (older setups `postgres://...`), which SQLAlchemy
    would otherwise map to psycopg2.
    """
    url = url.strip()
    if not url:
        return DEFAULT_DATABASE_URL
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def make_engine(url: str) -> Engine:
    """Create an engine with sensible settings for SQLite (dev/tests) or Postgres (prod)."""
    url = normalize_database_url(url)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Yield a database session for one request."""
    with SessionLocal() as session:
        yield session
