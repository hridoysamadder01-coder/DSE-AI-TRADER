from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()

_database_url = settings.database_url
_engine_kwargs = {"future": True}
if _database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    # Ensure the parent directory exists for relative SQLite URLs like
    # "sqlite:///./data/market.db". The Settings.data_dir property creates it.
    settings.data_dir  # noqa: B018
    # Resolve relative path to absolute so uvicorn's CWD doesn't matter.
    if _database_url.startswith("sqlite:///./") or _database_url.startswith(
        "sqlite:///.\\"
    ):
        rel = _database_url.replace("sqlite:///", "", 1)
        abs_path = (settings.project_root / rel).resolve()
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        _database_url = f"sqlite:///{abs_path.as_posix()}"

engine = create_engine(_database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """Create all tables. Safe to call on every startup."""
    from . import models  # noqa: F401  ensures models are registered

    Base.metadata.create_all(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
