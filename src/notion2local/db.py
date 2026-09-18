from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base


@dataclass
class Database:
    settings: Settings

    def __post_init__(self) -> None:
        connect_args = {}
        if self.settings.database_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        self.engine: Engine = create_engine(
            self.settings.database_url,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    def create_all(self) -> None:
        # Multiple Compose services bootstrap at the same time. PostgreSQL's
        # ``CREATE TABLE`` checks are not enough to make SQLAlchemy's
        # ``create_all`` race-safe, so serialize schema creation with a
        # transaction-scoped advisory lock. SQLite remains lock-free because
        # its test/dev database is single-process here.
        if self.engine.dialect.name == "postgresql":
            with self.engine.begin() as connection:
                connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 2_178_913_041})
                Base.metadata.create_all(connection)
            return
        Base.metadata.create_all(self.engine)

    def ping(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
