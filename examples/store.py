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

import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import (ForeignKey, String, Text, UniqueConstraint, create_engine,
                        func, select)
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


class ScoredArticle(Base):
    """A scored article, cached by its canonical URL so a shared read is scored once and reused
    across users. The scored fields (the ScoredRead interface) are stored as JSON verbatim."""

    __tablename__ = "scored_articles"

    url: Mapped[str] = mapped_column(String(2048), primary_key=True)
    scored: Mapped[str] = mapped_column(Text)           # JSON of the scored fields
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Read(Base):
    """One reading event — a scored article a user read. Idempotent per (user_id,
    canonical_url): submitting the same article again does not create a duplicate row (whether
    repeated reads should eventually matter is left as a future design decision). The scored
    fields are stored verbatim so the augmented corpus can be built without re-scoring."""

    __tablename__ = "reads"
    __table_args__ = (UniqueConstraint("user_id", "canonical_url", name="uq_read_user_url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    canonical_url: Mapped[str] = mapped_column(String(2048))
    scored: Mapped[str] = mapped_column(Text)           # JSON of the ScoredRead
    observed_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ApiToken(Base):
    """A per-user API token for non-browser clients (the browser extension; RSS later).

    Only the SHA-256 **hash** of the token is stored, so a database leak never yields a usable
    token. Each token is bound to one user and, when presented, resolves to that user's engine
    id so their reads flow through the *same* ingestion pipeline as the web app — the token is
    an authentication credential only, never a second code path. Revoking is deleting the row."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    label: Mapped[Optional[str]] = mapped_column(String(100), default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(default=None)


class RecEvent(Base):
    """A recommendation the engine surfaced to a user, and whether they opened it — the behavioral
    signal behind **Open-Mindedness**. One row per ``(user_id, article_id)``: surfacing the same
    recommendation again is idempotent (updates ``shown_at``, never clears ``opened_at``); opening
    it stamps ``opened_at``. ``cross_cutting`` marks a recommendation that bridges the reader across
    the centre — the reads whose *reception* (opened / surfaced) is the real-user analogue of the
    population's cross-cutting click-through. No recommendation is generated here; this only records
    the reception of recs the existing engine already produced."""

    __tablename__ = "rec_events"
    __table_args__ = (UniqueConstraint("user_id", "article_id", name="uq_recevent_user_article"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    article_id: Mapped[str] = mapped_column(String(2048))
    cross_cutting: Mapped[bool] = mapped_column(default=False)
    shown_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    opened_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
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

    # -- scored-article cache -------------------------------------------
    def get_scored_article(self, url: str) -> "dict | None":
        """Cached scoring for a canonical URL, or ``None`` if it hasn't been scored yet."""
        with self.session() as s:
            row = s.get(ScoredArticle, url)
            return dict(json.loads(row.scored)) if row is not None else None

    def save_scored_article(self, url: str, scored: dict) -> None:
        """Cache (upsert) the scoring for a canonical URL."""
        payload = json.dumps(scored)
        with self.session() as s:
            row = s.get(ScoredArticle, url)
            if row is None:
                s.add(ScoredArticle(url=url, scored=payload))
            else:
                row.scored = payload

    # -- reading events (idempotent per user + canonical URL) -----------
    def add_read(self, user_id: int, canonical_url: str, scored: dict,
                 observed_at: "str | None" = None) -> bool:
        """Record a reading event; return ``True`` if new, ``False`` if this (user, url) was
        already read — idempotent, no duplicate row."""
        with self.session() as s:
            exists = s.scalar(select(Read.id).where(Read.user_id == user_id,
                                                    Read.canonical_url == canonical_url))
            if exists is not None:
                return False
            s.add(Read(user_id=user_id, canonical_url=canonical_url,
                       scored=json.dumps(scored), observed_at=observed_at))
            return True

    def get_reads(self, user_id: int) -> list:
        """The user's scored reads (JSON verbatim), oldest first — the input to the augmented
        corpus."""
        with self.session() as s:
            rows = s.scalars(select(Read).where(Read.user_id == user_id)
                             .order_by(Read.id)).all()
            return [dict(json.loads(r.scored)) for r in rows]

    def count_reads(self, user_id: int) -> int:
        """How many distinct articles the user has read."""
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(Read)
                                .where(Read.user_id == user_id)) or 0)

    # -- recommendation reception (the Open-Mindedness feedback loop) ----
    def record_recommendations_shown(self, user_id: int, items, shown_at: "str | None" = None) -> int:
        """Record that recommendations were *surfaced* to a user — the denominator for
        Open-Mindedness. ``items`` is an iterable of ``(article_id, cross_cutting)`` from the recs
        the engine already produced. Idempotent per ``(user, article)``: a re-surfaced rec refreshes
        ``shown_at`` and never clears ``opened_at``. Returns how many rows were newly created."""
        stamp = shown_at or _utcnow().isoformat()
        new = 0
        with self.session() as s:
            for article_id, cross in items:
                aid = str(article_id)
                row = s.scalar(select(RecEvent).where(RecEvent.user_id == user_id,
                                                      RecEvent.article_id == aid))
                if row is None:
                    s.add(RecEvent(user_id=user_id, article_id=aid,
                                   cross_cutting=bool(cross), shown_at=stamp))
                    new += 1
                else:
                    row.shown_at = stamp
                    if cross:                       # only ever upgrade to cross-cutting, never down
                        row.cross_cutting = True
        return new

    def record_recommendation_open(self, user_id: int, article_id: str,
                                   cross_cutting: "bool | None" = None,
                                   opened_at: "str | None" = None) -> bool:
        """Record that a user *opened* a recommended article — the numerator. Idempotent: opening
        the same rec twice is a no-op after the first. If the open arrives before the surfacing was
        recorded (a race, or a direct open), the row is created using the caller's ``cross_cutting``
        hint. Returns ``True`` when this open is newly recorded."""
        stamp = opened_at or _utcnow().isoformat()
        aid = str(article_id)
        with self.session() as s:
            row = s.scalar(select(RecEvent).where(RecEvent.user_id == user_id,
                                                  RecEvent.article_id == aid))
            if row is None:
                s.add(RecEvent(user_id=user_id, article_id=aid,
                               cross_cutting=bool(cross_cutting), shown_at=stamp, opened_at=stamp))
                return True
            if cross_cutting:
                row.cross_cutting = True
            if row.opened_at is not None:
                return False
            row.opened_at = stamp
            return True

    def recommendation_reception(self, user_id: int) -> dict:
        """A user's **cross-cutting recommendation reception**: how many cross-cutting recs were
        surfaced (``shownCross``) and how many they opened (``openedCross``). ``rate`` =
        openedCross / shownCross is the real-user analogue of the population's cross-cutting
        click-through that Open-Mindedness ranks; ``None`` when none have been surfaced."""
        with self.session() as s:
            rows = s.scalars(select(RecEvent).where(RecEvent.user_id == user_id,
                                                    RecEvent.cross_cutting.is_(True))).all()
        shown = len(rows)
        opened = sum(1 for r in rows if r.opened_at is not None)
        return {"shownCross": shown, "openedCross": opened,
                "rate": (opened / shown) if shown else None}

    # -- per-user API tokens (browser extension / non-browser clients) --
    _TOKEN_PREFIX = "ih_"

    @staticmethod
    def _hash_token(token: str) -> str:
        """SHA-256 hex of a token — what we store and look up (never the plaintext)."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create_token(self, user_id: int, label: "str | None" = None) -> "tuple[str, dict]":
        """Mint a token for a user: store only its hash, return the **plaintext once** plus the
        row metadata (id / label / createdAt). The plaintext is unrecoverable afterwards."""
        token = self._TOKEN_PREFIX + secrets.token_urlsafe(32)
        with self.session() as s:
            row = ApiToken(token_hash=self._hash_token(token), user_id=user_id, label=label)
            s.add(row)
            s.flush()
            meta = {"id": row.id, "label": row.label,
                    "createdAt": row.created_at.isoformat() if row.created_at else None,
                    "lastUsedAt": None}
        return token, meta

    def resolve_token(self, token: str) -> "int | None":
        """The engine user id a token authorises, or ``None`` if unknown. Touches last_used_at.

        Look-up is by the token's hash, so the stored value is never the secret itself."""
        if not token:
            return None
        h = self._hash_token(token)
        with self.session() as s:
            row = s.scalar(select(ApiToken).where(ApiToken.token_hash == h))
            if row is None:
                return None
            row.last_used_at = _utcnow()
            return int(row.user_id)

    def list_tokens(self, user_id: int) -> list:
        """A user's tokens (metadata only — never the plaintext or hash), oldest first."""
        with self.session() as s:
            rows = s.scalars(select(ApiToken).where(ApiToken.user_id == user_id)
                             .order_by(ApiToken.id)).all()
            return [{"id": r.id, "label": r.label,
                     "createdAt": r.created_at.isoformat() if r.created_at else None,
                     "lastUsedAt": r.last_used_at.isoformat() if r.last_used_at else None}
                    for r in rows]

    def revoke_token(self, user_id: int, token_id: int) -> bool:
        """Delete a user's token; return ``True`` if it existed and belonged to them."""
        with self.session() as s:
            row = s.get(ApiToken, token_id)
            if row is None or row.user_id != user_id:
                return False
            s.delete(row)
            return True


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
