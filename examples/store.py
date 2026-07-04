"""SQLite persistence for the Information Health product engine.

The research pipeline (``rwe/``, ``health_report``, ``narrate_report``) is stateless and
stays untouched. This module is the *product* layer's durable store — the real users, and
the third-party identities (Google OAuth) that map onto them — that the closed beta needs
so a reader's state survives between visits.

It is deliberately minimal and grows one table at a time as the beta milestones land
(reading history, a scored-article cache, report / recommendation snapshots). Nothing here
touches the recommendation or health-report algorithms or the JSON API contract.

Storage is **SQLite via SQLAlchemy** — a real, durable, file-backed database with no server
to run. The backing store is chosen entirely by one environment variable, ``RWE_DB_URL``
(default ``sqlite:///<repo>/data/ih_beta.db``); moving to PostgreSQL after the beta is only a
change to that URL, not to this code.

    from store import Store
    store = Store()                      # RWE_DB_URL, or the default sqlite file
    user = store.upsert_user_by_identity("google", "1234567890",
                                         email="a@b.com", display_name="Ada")
    same = store.get_user(user.id)       # -> the same row
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import (ForeignKey, String, Text, UniqueConstraint, create_engine,
                        select)
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            relationship, sessionmaker)
from sqlalchemy.pool import StaticPool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def default_db_url() -> str:
    """Repo-local SQLite file (``<repo>/data/ih_beta.db``) unless ``RWE_DB_URL`` overrides.

    The default path is under ``data/`` (git-ignored) so a running engine never writes a
    tracked file. Only this URL changes to point at PostgreSQL later — no code here does."""
    env = os.environ.get("RWE_DB_URL")
    if env:
        return env
    repo_root = Path(__file__).resolve().parent.parent
    return f"sqlite:///{repo_root / 'data' / 'ih_beta.db'}"


class Base(DeclarativeBase):
    pass


class User(Base):
    """A real person using the product.

    ``id`` is the stable engine user id the whole product layer keys on — reading history,
    reports, and preferences all reference it. Email / display name are profile context."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), default=None)
    display_name: Mapped[Optional[str]] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    identities: Mapped[list["Identity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")


class Identity(Base):
    """A third-party login (e.g. Google OAuth) mapped to a :class:`User`.

    Uniqueness is on ``(provider, provider_account_id)`` so the same Google account always
    resolves to the same user; email is stored as profile context, not as the join key
    (a user could later add other providers for the same address)."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "provider_account_id",
                                       name="uq_identity_provider_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_account_id: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    user: Mapped[User] = relationship(back_populates="identities")


class Onboarding(Base):
    """A user's onboarding choices — the publishers they selected. One row per user
    (upserted); the seed for their Initial Information Health Estimate."""

    __tablename__ = "onboarding"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    outlets: Mapped[str] = mapped_column(Text)          # JSON list of outlet ids
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ReportSnapshot(Base):
    """A stored Information Health result for a user — an estimate or a measured report.
    Append-only: the latest row is the current result, and the history feeds later
    comparison. The full JSON is kept verbatim so the frontend renders it unchanged."""

    __tablename__ = "report_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))       # "estimate" | "measured"
    overall: Mapped[int] = mapped_column()
    snapshot: Mapped[str] = mapped_column(Text)         # JSON of the full report
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Store:
    """A durable store bound to one database URL.

    A single ``Store`` owns a connection pool + session factory; share one instance across
    the app. Every write goes through :meth:`session` (a commit-or-rollback scope) so callers
    never manage transactions by hand. Returned ORM objects are detached but fully loaded
    (``expire_on_commit=False``), so their scalar fields are safe to read after the call."""

    def __init__(self, url: str | None = None):
        self.url = url or default_db_url()
        self.engine = _make_engine(self.url)
        Base.metadata.create_all(self.engine)
        self._Session = sessionmaker(bind=self.engine, expire_on_commit=False,
                                     future=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self._Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # -- repository operations ------------------------------------------
    def upsert_user_by_identity(self, provider: str, provider_account_id: str,
                                email: str | None = None,
                                display_name: str | None = None) -> User:
        """Return the user for ``(provider, provider_account_id)``, creating the user +
        identity on first sign-in.

        Idempotent: the same identity always resolves to the same user. A returning user's
        email / display name are refreshed when a value is supplied (Google can change them),
        and left as-is when ``None``."""
        with self.session() as s:
            identity = s.scalar(
                select(Identity).where(Identity.provider == provider,
                                       Identity.provider_account_id == provider_account_id))
            if identity is None:
                user = User(email=email, display_name=display_name)
                s.add(user)
                s.flush()                       # assign user.id
                s.add(Identity(provider=provider,
                               provider_account_id=provider_account_id, user_id=user.id))
            else:
                user = identity.user
                if email is not None:
                    user.email = email
                if display_name is not None:
                    user.display_name = display_name
            s.flush()
            s.refresh(user)
            return user

    def get_user(self, user_id: int) -> User | None:
        """The user row for ``user_id``, or ``None`` if there is no such user."""
        with self.session() as s:
            return s.get(User, user_id)

    # -- onboarding choices + report snapshots --------------------------
    def save_onboarding(self, user_id: int, outlets: list[str]) -> None:
        """Persist (upsert) the publishers a user selected during onboarding."""
        payload = json.dumps(list(outlets))
        with self.session() as s:
            row = s.get(Onboarding, user_id)
            if row is None:
                s.add(Onboarding(user_id=user_id, outlets=payload))
            else:
                row.outlets = payload
                row.updated_at = _utcnow()

    def get_onboarding(self, user_id: int) -> "list[str] | None":
        """The user's selected outlets, or ``None`` if they haven't onboarded."""
        with self.session() as s:
            row = s.get(Onboarding, user_id)
            return list(json.loads(row.outlets)) if row is not None else None

    def save_report(self, user_id: int, report: dict) -> None:
        """Append a report/estimate snapshot for a user (the JSON is stored verbatim)."""
        with self.session() as s:
            s.add(ReportSnapshot(user_id=user_id, mode=str(report.get("mode", "")),
                                 overall=int(report.get("overall", 0) or 0),
                                 snapshot=json.dumps(report)))

    def latest_report(self, user_id: int) -> "dict | None":
        """The user's most recent stored report/estimate, or ``None``."""
        with self.session() as s:
            row = s.scalar(select(ReportSnapshot)
                           .where(ReportSnapshot.user_id == user_id)
                           .order_by(ReportSnapshot.id.desc()))
            return dict(json.loads(row.snapshot)) if row is not None else None


def _make_engine(url: str):
    """Create the SQLAlchemy engine for ``url``.

    SQLite needs ``check_same_thread=False`` because one engine is shared across FastAPI's
    request threadpool; an in-memory URL additionally needs a single shared connection
    (``StaticPool``) or each session would see its own empty database. A file-backed URL has
    its parent directory created so the first run works from a clean checkout."""
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            return create_engine(url, future=True, connect_args=connect_args,
                                 poolclass=StaticPool)
        path = url.split("sqlite:///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, future=True, connect_args=connect_args)
    return create_engine(url, future=True)
