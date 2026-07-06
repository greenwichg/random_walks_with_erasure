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
import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import (ForeignKey, String, Text, UniqueConstraint, create_engine,
                        event, func, select)
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


class UserSettings(Base):
    """A user's **product preferences** — theme, notification / digest / privacy toggles, and reading
    goal. Deliberately its own table, kept separate from any health-report state (report snapshots):
    this row is application preference only, never a metric or a reading event. One row per user
    (upserted); the whole preferences object is stored as JSON verbatim, so adding a preference field
    needs no migration and any field a user hasn't set falls back to server defaults at read time.

    Nothing here is wired into the recommender or health algorithms — it only persists preferences."""

    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    settings: Mapped[str] = mapped_column(Text)          # JSON of the preferences object
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


class FeedArticle(Base):
    """An article discovered via RSS ingestion — the news **catalog** (distinct from per-user
    ``reads``). Deduplicated by ``canonical_url`` (the same key ``reads`` and the scoring cache use),
    so re-polling a feed never creates a duplicate. It preserves what RSS carries that the scored
    model does not — the real publisher article URL, the publisher, the publication timestamp, the
    title, the description, and (when the feed provides it) the body — alongside the ``scored`` JSON
    produced by the SAME scorer the reading pipeline uses. Nothing here feeds the recommender yet:
    this is the data foundation a future real-article recommender / discover surface will draw from,
    so it is deliberately isolated from the corpus, the report, and the recommendation algorithms."""

    __tablename__ = "feed_articles"

    canonical_url: Mapped[str] = mapped_column(String(2048), primary_key=True)   # dedup key
    url: Mapped[str] = mapped_column(String(2048))                     # the original publisher URL
    publisher: Mapped[str] = mapped_column(String(255), default="")    # resolved canonical outlet
    source_publisher: Mapped[Optional[str]] = mapped_column(String(255), default=None)  # raw feed title
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[Optional[str]] = mapped_column(Text, default=None)    # content:encoded, when present
    published_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)  # article pubDate (ISO)
    source_feed: Mapped[str] = mapped_column(String(2048), default="")  # feed URL it came from
    scored: Mapped[str] = mapped_column(Text)                          # JSON of the ScoredRead
    fetched_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
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

    # -- product preferences (settings) — never health-report state ----
    def get_settings(self, user_id: int) -> "dict | None":
        """The user's stored preferences (JSON verbatim), or ``None`` if they've never saved any —
        in which case the caller supplies server defaults. Preferences only; no metric state."""
        with self.session() as s:
            row = s.get(UserSettings, user_id)
            return dict(json.loads(row.settings)) if row is not None else None

    def save_settings(self, user_id: int, settings: dict) -> None:
        """Persist (upsert) a user's preferences — the caller passes the already-normalised object,
        stored verbatim so future preference fields need no migration."""
        payload = json.dumps(dict(settings))
        with self.session() as s:
            row = s.get(UserSettings, user_id)
            if row is None:
                s.add(UserSettings(user_id=user_id, settings=payload))
            else:
                row.settings = payload
                row.updated_at = _utcnow()

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

    def list_report_snapshots(self, user_id: int, limit: int = 60) -> list:
        """The user's report/estimate snapshots as compact trend points (``overall`` over time),
        **oldest-first**, capped at the most recent ``limit`` — the source for the dashboard health
        trend and the analytics score history. The full snapshot JSON is not loaded (only the id,
        mode, overall, and timestamp), keeping the trend query cheap."""
        with self.session() as s:
            rows = s.scalars(select(ReportSnapshot)
                             .where(ReportSnapshot.user_id == user_id)
                             .order_by(ReportSnapshot.id)).all()
        rows = rows[-limit:] if limit else rows
        return [{"id": r.id, "mode": r.mode, "overall": int(r.overall),
                 "createdAt": r.created_at.isoformat() if r.created_at else None} for r in rows]

    def report_metric_series(self, user_id: int, limit: int = 60) -> list:
        """Per-snapshot analytics inputs: each saved report's ``overall``, per-metric ``{key: score}``,
        and emotion ``attention``, **oldest-first** (capped at the most recent ``limit``). Parses the
        stored snapshot JSON (the full report as saved) — no metric is recomputed, so analytics only
        ever *visualises* what the engine already computed. ``date`` is the snapshot's UTC day."""
        with self.session() as s:
            rows = s.scalars(select(ReportSnapshot)
                             .where(ReportSnapshot.user_id == user_id)
                             .order_by(ReportSnapshot.id)).all()
        rows = rows[-limit:] if limit else rows
        out = []
        for r in rows:
            try:
                rep = json.loads(r.snapshot)
            except (TypeError, ValueError):
                continue
            metrics = {m.get("key"): m.get("score") for m in (rep.get("metrics") or [])
                       if m.get("key") is not None and m.get("score") is not None}
            out.append({"date": r.created_at.isoformat()[:10] if r.created_at else None,
                        "overall": int(r.overall), "metrics": metrics,
                        "attention": rep.get("attention") or {}})
        return out

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

    # -- news catalog (RSS ingestion; deduped by canonical URL) ---------
    def upsert_feed_article(self, *, canonical_url: str, url: str, publisher: str,
                            source_publisher: "str | None", title: str, description: str,
                            body: "str | None", published_at: "str | None", source_feed: str,
                            scored: dict) -> bool:
        """Insert a catalog article, or refresh an existing one (dedup by ``canonical_url``). Returns
        ``True`` when newly created, ``False`` on a re-poll. A re-poll refreshes ``fetched_at`` and
        backfills any field that was empty before, but never rewrites first-seen metadata."""
        payload = json.dumps(scored)
        with self.session() as s:
            row = s.get(FeedArticle, canonical_url)
            if row is None:
                s.add(FeedArticle(
                    canonical_url=canonical_url, url=url, publisher=publisher,
                    source_publisher=source_publisher, title=title, description=description,
                    body=body, published_at=published_at, source_feed=source_feed, scored=payload))
                return True
            row.fetched_at = _utcnow()
            if title and not row.title:
                row.title = title
            if description and not row.description:
                row.description = description
            if body and not row.body:
                row.body = body
            if published_at and not row.published_at:
                row.published_at = published_at
            return False

    def get_feed_article(self, canonical_url: str) -> "dict | None":
        """One catalog article (all fields + parsed ``scored``), or ``None``."""
        with self.session() as s:
            r = s.get(FeedArticle, canonical_url)
            return self._feed_row(r) if r is not None else None

    def count_feed_articles(self) -> int:
        """How many distinct catalog articles have been ingested."""
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(FeedArticle)) or 0)

    def list_feed_articles(self, limit: int = 50) -> list:
        """Catalog articles, most-recently-fetched first (capped at ``limit``)."""
        with self.session() as s:
            rows = s.scalars(select(FeedArticle)
                             .order_by(FeedArticle.fetched_at.desc())
                             .limit(limit)).all()
            return [self._feed_row(r) for r in rows]

    @staticmethod
    def _feed_row(r: "FeedArticle") -> dict:
        return {"canonicalUrl": r.canonical_url, "url": r.url, "publisher": r.publisher,
                "sourcePublisher": r.source_publisher, "title": r.title,
                "description": r.description, "body": r.body, "publishedAt": r.published_at,
                "sourceFeed": r.source_feed, "scored": dict(json.loads(r.scored)),
                "fetchedAt": r.fetched_at.isoformat() if r.fetched_at else None}

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

    def list_reads(self, user_id: int) -> list:
        """The user's reads with row metadata for the reading-history view — **newest first**.

        Each entry carries the stable row ``id``, the canonical URL, the verbatim scored fields, and
        the observed / created timestamps. Complements :meth:`get_reads` (scored payloads only,
        oldest-first, for the augmented corpus); this is the display-oriented projection the history
        API serialises."""
        with self.session() as s:
            rows = s.scalars(select(Read).where(Read.user_id == user_id)
                             .order_by(Read.id.desc())).all()
            return [{"id": r.id, "canonicalUrl": r.canonical_url,
                     "scored": dict(json.loads(r.scored)),
                     "observedAt": r.observed_at,
                     "createdAt": r.created_at.isoformat() if r.created_at else None}
                    for r in rows]

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

    def list_rec_events(self, user_id: int) -> list:
        """All of a user's recommendation events (surfaced / opened timestamps + cross-cutting flag),
        oldest-first — the source for the analytics recommendation-acceptance series."""
        with self.session() as s:
            rows = s.scalars(select(RecEvent).where(RecEvent.user_id == user_id)
                             .order_by(RecEvent.id)).all()
            return [{"shownAt": r.shown_at, "openedAt": r.opened_at,
                     "crossCutting": bool(r.cross_cutting)} for r in rows]

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

    # -- storage / durability diagnostics -------------------------------
    def storage_diagnostics(self) -> dict:
        """A read-only snapshot of the storage backend for ops: the (redacted) URL, whether it is
        ephemeral, the SQLite journal mode + key pragmas actually in effect, the on-disk size, a
        fast corruption probe (``PRAGMA quick_check``), and backup status (count + newest). Safe to
        call on a live database."""
        info: dict = {"url": _redact_url(self.url), "backend": self.engine.dialect.name,
                      "ephemeral": is_ephemeral_url(self.url)}
        if self.url.startswith("sqlite"):
            with self.engine.connect() as c:
                info["journalMode"] = c.exec_driver_sql("PRAGMA journal_mode").scalar()
                info["foreignKeys"] = bool(c.exec_driver_sql("PRAGMA foreign_keys").scalar())
                info["busyTimeoutMs"] = int(c.exec_driver_sql("PRAGMA busy_timeout").scalar() or 0)
                info["synchronous"] = c.exec_driver_sql("PRAGMA synchronous").scalar()
                info["quickCheck"] = c.exec_driver_sql("PRAGMA quick_check").scalar()
            path = sqlite_path(self.url)
            if path and os.path.exists(path):
                info["sizeBytes"] = os.path.getsize(path)
        try:                                    # backup status is best-effort — never fail diagnostics
            backups = list_backups(default_backup_dir(self.url))
            info["backupCount"] = len(backups)
            info["lastBackupAt"] = backups[0]["modifiedAt"] if backups else None
        except Exception:
            pass
        return info


# SQLite pragmas applied to EVERY connection for durability + concurrency. journal_mode is
# persistent on the file (setting it again is a harmless no-op); the rest are per-connection and
# must be re-applied on each connect. Documented in DEPLOYMENT.md → "SQLite settings":
#
#   journal_mode=WAL   Write-Ahead Logging: readers never block the writer (and vice-versa), and a
#                      crash mid-write recovers cleanly — the durability/concurrency core.
#   synchronous=NORMAL The WAL-recommended sync level: durable across an application/OS crash; a
#                      sudden power loss may drop the last un-checkpointed transaction but never
#                      corrupts the file. (FULL is safer but fsyncs every commit; NORMAL is the
#                      documented WAL default and far faster.)
#   busy_timeout=5000  On lock contention, wait up to 5s for the lock instead of immediately
#                      raising "database is locked".
#   foreign_keys=ON    Enforce foreign keys (SQLite defaults them OFF per connection).
SQLITE_PRAGMAS = (
    ("journal_mode", "WAL"),
    ("synchronous", "NORMAL"),
    ("busy_timeout", "5000"),
    ("foreign_keys", "ON"),
)


def _apply_sqlite_pragmas(dbapi_conn, _record) -> None:
    """SQLAlchemy ``connect`` listener: set the durability pragmas on each new SQLite connection."""
    cur = dbapi_conn.cursor()
    try:
        for name, value in SQLITE_PRAGMAS:
            cur.execute(f"PRAGMA {name}={value}")
    finally:
        cur.close()


def _make_engine(url: str):
    """Create the SQLAlchemy engine for ``url``.

    SQLite needs ``check_same_thread=False`` because one engine is shared across FastAPI's
    request threadpool; an in-memory URL additionally needs a single shared connection
    (``StaticPool``) or each session would see its own empty database. A file-backed URL has
    its parent directory created so the first run works from a clean checkout. Every SQLite
    connection gets the durability pragmas (:data:`SQLITE_PRAGMAS`) via a ``connect`` listener."""
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            engine = create_engine(url, future=True, connect_args=connect_args,
                                   poolclass=StaticPool)
        else:
            path = url.split("sqlite:///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url, future=True, connect_args=connect_args)
        event.listen(engine, "connect", _apply_sqlite_pragmas)
        return engine
    return create_engine(url, future=True)


# --------------------------------------------------------------------------- #
# Storage introspection + backup / restore (Private Alpha durability).
# --------------------------------------------------------------------------- #
def sqlite_path(url: str) -> "str | None":
    """The filesystem path of a file-backed SQLite URL, or ``None`` for in-memory / non-SQLite."""
    if not url or not url.startswith("sqlite") or ":memory:" in url or url == "sqlite://":
        return None
    return url.split("sqlite:///", 1)[-1] or None


_EPHEMERAL_DIRS = ("/tmp/", "/var/tmp/", "/dev/shm/")


def is_ephemeral_url(url: str) -> bool:
    """Whether a DB URL points at storage that does not survive a restart/redeploy — an in-memory
    SQLite database, or a file under an obviously-ephemeral temp directory. Used to refuse a
    production start that would silently lose data. A file under a mounted volume is NOT ephemeral;
    that guarantee comes from the deployment (docker-compose mounts a named volume)."""
    u = (url or "").strip()
    if not u.startswith("sqlite"):
        return False
    if u in {"sqlite://", "sqlite:///:memory:"} or ":memory:" in u:
        return True
    path = u.split("sqlite:///", 1)[-1]
    return any(path.startswith(d) or path.startswith(d[1:]) for d in _EPHEMERAL_DIRS)


def _redact_url(url: str) -> str:
    """Hide any password in a ``scheme://user:pass@host`` URL; SQLite file URLs are unchanged."""
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:***@", url or "")


def integrity_ok(db_path: str) -> bool:
    """True iff ``PRAGMA integrity_check`` reports a single ``ok`` for the SQLite file — the check
    run on a backup before it is trusted, and before a restore replaces the live database."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("PRAGMA integrity_check").fetchall()
        return len(rows) == 1 and rows[0][0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        con.close()


def backup_database(db_path: str, dest_path: str) -> None:
    """Consistent **online** backup of a live SQLite DB using the sqlite3 backup API, which copies
    pages while the database is in use — the server keeps running. Writes to a temp file then
    atomically renames, so a partial backup is never published under ``dest_path``. The source is
    opened normally (not read-only): the backup API only reads it, and a strict read-only handle
    cannot read a WAL database because it needs the ``-shm`` file."""
    src = sqlite3.connect(db_path)
    tmp = dest_path + ".tmp"
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)                 # online snapshot: consistent, no server stop
    finally:
        dst.close()
        src.close()
    os.replace(tmp, dest_path)          # atomic publish


def restore_database(backup_path: str, db_path: str) -> str:
    """Replace the active DB with ``backup_path``, **safely**: verify the backup's integrity FIRST
    (raise, untouched, if bad), snapshot the current DB to a ``.pre-restore`` sidecar, then
    atomically swap and drop stale ``-wal``/``-shm`` sidecars so the restored file is authoritative.
    Returns the pre-restore snapshot path (or ""). The engine must be STOPPED during a restore."""
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"backup not found: {backup_path}")
    if not integrity_ok(backup_path):
        raise ValueError(f"refusing to restore: {backup_path} failed its integrity check")
    saved = ""
    if os.path.exists(db_path):
        saved = db_path + ".pre-restore"
        shutil.copy2(db_path, saved)
    tmp = db_path + ".restore.tmp"
    shutil.copy2(backup_path, tmp)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, db_path)
    for side in ("-wal", "-shm"):
        try:
            os.remove(db_path + side)
        except FileNotFoundError:
            pass
    return saved


def default_backup_dir(db_url: str) -> str:
    """Where backups go: ``RWE_BACKUP_DIR`` if set, else a ``backups/`` folder beside the DB file,
    else ``<repo>/backups`` (in-memory / no file path)."""
    env = os.environ.get("RWE_BACKUP_DIR")
    if env:
        return env
    path = sqlite_path(db_url)
    if path:
        return str(Path(path).resolve().parent / "backups")
    return str(Path(__file__).resolve().parent.parent / "backups")


def list_backups(out_dir: str) -> list:
    """Backups in ``out_dir`` (``*.db``), newest first, with size + mtime."""
    p = Path(out_dir)
    if not p.is_dir():
        return []
    out = []
    for f in sorted(p.glob("*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = f.stat()
        out.append({"path": str(f), "sizeBytes": st.st_size,
                    "modifiedAt": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return out


def create_backup(db_url: "str | None" = None, out_dir: "str | None" = None) -> str:
    """Back up the configured database to a timestamped file and return its path. Verifies the
    backup's integrity and discards it if the check fails (never leaves a bad backup behind)."""
    url = db_url or default_db_url()
    path = sqlite_path(url)
    if path is None:
        raise ValueError("cannot back up an in-memory or non-file database")
    if not os.path.exists(path):
        raise FileNotFoundError(f"database file not found: {path}")
    out = out_dir or default_backup_dir(url)
    os.makedirs(out, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = os.path.join(out, f"{Path(path).stem}-{ts}.db")
    backup_database(path, dest)
    if not integrity_ok(dest):
        os.remove(dest)
        raise RuntimeError("backup failed its integrity check and was discarded")
    return dest
