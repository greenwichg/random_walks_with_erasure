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
import math
import os
import re
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import (ForeignKey, String, Text, UniqueConstraint, and_, create_engine,
                        delete, event, func, or_, select, text)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            relationship, sessionmaker)
from sqlalchemy.pool import StaticPool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _improvement_lifecycle_to_dict(r) -> dict:
    """One ``ImprovementLifecycle`` row → the camelCase dict the reconciler and web tier speak (RC2.3)."""
    return {"recKey": r.rec_key, "metric": r.metric, "state": r.state,
            "firstScore": r.first_score, "currentScore": r.current_score,
            "completedScore": r.completed_score,
            "generatedAt": r.generated_at, "shownAt": r.shown_at, "viewedAt": r.viewed_at,
            "acceptedAt": r.accepted_at, "dismissedAt": r.dismissed_at,
            "completedAt": r.completed_at, "expiredAt": r.expired_at,
            "supersededAt": r.superseded_at, "supersededBy": r.superseded_by,
            "updatedAt": r.updated_at}


def _json_safe(obj):
    """Recursively replace non-finite floats (``NaN`` / ``Infinity`` / ``-Infinity``) with
    ``None`` so the value serialises to RFC-8259-valid JSON. Recurses ``dict`` values and
    ``list`` items; every finite number and every non-float value passes through unchanged.

    Scored reads carry ``NaN`` sentinels by design: ``confidence`` / ``register`` default to
    ``NaN`` when a read isn't enriched, and an outlet the registry doesn't know scores ``lean``
    as ``NaN`` (see ``ingest``/``augmented_corpus``). Those mean "unknown", and every consumer
    already treats a missing value and ``NaN`` identically (``discover._num`` falls back to its
    default; ``feed_source._bias_label`` drops the row on either). But ``json.dumps`` at its
    default ``allow_nan=True`` emits the bare tokens ``NaN`` / ``Infinity`` / ``-Infinity``,
    which are not valid JSON and which SQLite's ``json_extract`` / ``json_valid`` reject as
    "malformed JSON". Mapping them to ``null`` keeps the stored document valid while preserving
    that "unknown" meaning — it changes neither scoring nor recommendation behaviour."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def _dumps_scored(scored) -> str:
    """The single serialisation path for scored JSON. Sanitises non-finite floats, then dumps,
    so no invalid-JSON document can ever be persisted — ``json_valid(scored) = 1`` holds for
    every stored row. All ``scored`` writes in this module go through here."""
    return json.dumps(_json_safe(scored))


# Multi-source media-merge priority: when the same canonical URL arrives from several ingestion sources,
# the image from the higher-priority source wins (see ``upsert_feed_article``). Centralised here so the
# ordering changes in ONE place with **no data migration** — precedence is derived dynamically from each
# row's ``source_type`` (nothing extra is persisted). Env-overridable, e.g.
# ``RWE_SOURCE_PRIORITY="rss:100,newsapi:80,gdelt:60,extension:40"``. Unknown / absent -> 0 (lowest).
# ``extension`` (a user's browser reporting standard page metadata) ranks below every real feed, so a
# feed that later discovers the same article upgrades its media on merge — never the reverse.
SOURCE_PRIORITY = {"rss": 100, "newsapi": 80, "gdelt": 60, "extension": 40}


def _source_priority_map() -> dict:
    raw = os.environ.get("RWE_SOURCE_PRIORITY", "").strip()
    if not raw:
        return SOURCE_PRIORITY
    out = dict(SOURCE_PRIORITY)
    for part in raw.split(","):
        k, _, v = part.partition(":")
        if v.strip().lstrip("-").isdigit():
            out[k.strip().lower()] = int(v.strip())
    return out


def _media_priority(source_type) -> int:
    """Media precedence for a ``source_type`` (higher wins on merge); unknown / ``None`` -> 0."""
    return _source_priority_map().get((source_type or "").lower(), 0)


# The image_source tags RSS/Atom ingestion emits (via ``media.pick_best_image``): the ``media:`` media
# tags plus ``enclosure`` and the Atom image link. Kept here so precedence never relies on an implicit
# "anything unrecognised must be RSS" assumption — it is an explicit, closed contract.
_RSS_IMAGE_TAGS = frozenset({"enclosure", "atom:link"})


def normalize_image_source(image_source: "str | None") -> str:
    """Normalise a stored ``FeedArticle.image_source`` to the **ingestion source** that supplied the
    image — one of ``"rss"`` | ``"newsapi"`` | ``"gdelt"`` | ``"unknown"``.

    A non-RSS adapter tags its image with its own ``source_type`` (``newsapi`` / ``gdelt``); RSS/Atom
    tags it with a media tag (``media:content`` / ``media:thumbnail`` / ``enclosure`` / ``atom:link``),
    all of which map to ``"rss"``. Anything unrecognised (or absent) is ``"unknown"`` — it never
    inherits RSS priority by accident. This is the single place the mapping lives; ``SOURCE_PRIORITY``
    remains the single source of truth for the numbers."""
    s = (image_source or "").strip().lower()
    if s in ("rss", "newsapi", "gdelt"):
        return s
    if s.startswith("media:") or s in _RSS_IMAGE_TAGS:
        return "rss"
    return "unknown"


def _stored_image_priority(image_source, origin_source_type=None) -> int:
    """Media priority of the source that supplied the **currently stored** image. ``image_source`` is
    refreshed every time the image is replaced, so it — not the article's origin ``source_type`` — is
    the correct precedence key (otherwise a GDELT-origin row whose image was upgraded to RSS could be
    wrongly overwritten by NewsAPI). The source is resolved by :func:`normalize_image_source`; an
    unrecognised tag is ``"unknown"`` (priority 0), and a truly absent tag (legacy rows) falls back to
    the row's origin. Precedence is always looked up in ``SOURCE_PRIORITY`` — nothing numeric is
    persisted."""
    if not (image_source or "").strip():
        return _media_priority(origin_source_type)          # legacy: no image_source recorded
    return _media_priority(normalize_image_source(image_source))


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
    # Additive read-source attribution (Commit 14) — all nullable, metadata ONLY (no consumer
    # branches on them): read_source = app | extension | <future import>; opened_from = the in-app
    # surface (recommendations/discover/stories/search/saved); device = optional client hint. Legacy
    # rows and the browser extension keep NULL here, so every existing reader/consumer is unchanged.
    read_source: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    opened_from: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    device: Mapped[Optional[str]] = mapped_column(String(64), default=None)
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


# The explicit feedback a reader can give a recommendation card. Canonical (snake_case) wire values;
# the web tier maps its own "read-later" hyphen form onto "read_later" before calling.
RECOMMENDATION_FEEDBACK_TYPES = ("like", "dislike", "ignore", "read_later")


class RecFeedback(Base):
    """A reader's explicit feedback on a recommendation the engine already produced — ``like`` /
    ``dislike`` / ``ignore`` / ``read_later``. One row per ``(user_id, article_id, feedback)``:
    repeating the same signal is idempotent (refreshes ``updated_at``), while distinct feedback types
    on one article are distinct rows, so a reader's full set of signals is preserved without collapsing
    contradictory ones. **Recorded only** (B1): no recommender, ranking, report, or personalization
    path reads this table — it is a truthful capture of an interaction the card already exposes, kept
    for a future consumer to decide how (if at all) to weigh it."""

    __tablename__ = "rec_feedback"
    __table_args__ = (UniqueConstraint("user_id", "article_id", "feedback",
                                       name="uq_recfeedback_user_article_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    article_id: Mapped[str] = mapped_column(String(2048))
    feedback: Mapped[str] = mapped_column(String(16))       # one of RECOMMENDATION_FEEDBACK_TYPES
    created_at: Mapped[str] = mapped_column(String(64))     # ISO — first time this signal was given
    updated_at: Mapped[str] = mapped_column(String(64))     # ISO — last time it was (re)submitted


class ImprovementLifecycle(Base):
    """The lifecycle ledger for one improvement recommendation for one reader (RC2.3).

    Identity is ``(user_id, rec_key)`` where ``rec_key`` is the recommendation's stable id
    (``imp_<metric>``): the recommendation to improve a given metric keeps the **same row** across
    report regenerations, so its whole history lives in one place (and survives the Estimate→Measured
    transition, since the key is metric-based). Each transition stamps its own timestamp column and a
    stamped column is never cleared, so a future evaluation can reconstruct the ordered history from the
    columns. ``first_score`` is the metric score when the recommendation was first generated and
    ``completed_score`` the score at completion — the two ends of the deterministic completion rule.
    Product/lifecycle state only: it drives no recommender, ranking, selection, or report computation
    (RC2.3 records; later RC2 phases may consume)."""

    __tablename__ = "improvement_lifecycle"
    __table_args__ = (UniqueConstraint("user_id", "rec_key", name="uq_improvement_user_reckey"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    rec_key: Mapped[str] = mapped_column(String(64))
    metric: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16))
    first_score: Mapped[Optional[int]] = mapped_column(default=None)
    current_score: Mapped[Optional[int]] = mapped_column(default=None)
    completed_score: Mapped[Optional[int]] = mapped_column(default=None)
    generated_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    shown_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    viewed_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    accepted_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    dismissed_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    completed_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    expired_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    superseded_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    superseded_by: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)


#: The canonical lifecycle events an improvement recommendation can receive via the API (RC2.3). The
#: reconciler owns the derived states (generated/shown/in_progress/completed/expired/superseded); these
#: three are the explicit reader signals the endpoints record.
IMPROVEMENT_LIFECYCLE_EVENTS = {"accepted": "accepted_at", "dismissed": "dismissed_at",
                                "viewed": "viewed_at"}


class SavedArticle(Base):
    """An article a user explicitly **saved** — the single "Saved" concept (there is no separate
    bookmark). One row per ``(user_id, article_id)``: saving the same article twice is idempotent
    (the duplicate is ignored) and unsaving deletes the row. ``article`` is a JSON snapshot of the
    Article the reader saw, so the saved list renders without re-fetching the catalog. Per-user and
    self-contained — it touches no recommender, report, corpus, ingestion, or clustering path."""

    __tablename__ = "saved_articles"
    __table_args__ = (UniqueConstraint("user_id", "article_id", name="uq_saved_user_article"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    article_id: Mapped[str] = mapped_column(String(2048))
    article: Mapped[str] = mapped_column(Text)          # JSON snapshot of the saved Article
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Notification(Base):
    """A materialised notification for a user — one row per due notification the delivery boundary
    produced. Idempotent per ``(user_id, kind, dedupe_key)`` (enforced by the DB constraint below):
    re-evaluating on every fetch never duplicates a row, and the set of stored ``dedupe_key``s is the
    idempotency **ledger** the boundary reads back to suppress re-delivery. ``body`` is the JSON-safe
    notification object stored **verbatim** — this table persists dicts, exactly like
    ``report_snapshots`` / ``user_settings``, so nothing about the ``notification_service`` leaf is
    imported here. ``created_at`` is the notification's own (injected, deterministic) timestamp, kept
    verbatim; ``seen_at`` is the read-state (set by the in-app inbox later, never in this milestone);
    ``recorded_at`` is when the row was physically written. Product state only — it touches no
    recommender, report, or corpus path."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "kind", "dedupe_key",
                                       name="uq_notification_user_kind_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)              # JSON of the notification (payload/title/gate)
    created_at: Mapped[str] = mapped_column(String(64))  # the notification's own (injected) timestamp
    seen_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)   # read-state (set later)
    recorded_at: Mapped[datetime] = mapped_column(default=_utcnow)             # DB write time


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
    # Media metadata (additive; RSS/Atom only, canonical URL, no binary storage). All None when absent.
    image: Mapped[Optional[str]] = mapped_column(String(2048), default=None)
    image_width: Mapped[Optional[int]] = mapped_column(default=None)
    image_height: Mapped[Optional[int]] = mapped_column(default=None)
    image_mime: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    image_source: Mapped[Optional[str]] = mapped_column(String(255), default=None)   # winning media tag
    image_attribution: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    # Source attribution (additive; multi-source ingestion). All None on legacy / RSS-only rows until set;
    # downstream stays source-agnostic — these are provenance/diagnostics + the media-merge priority key.
    source_type: Mapped[Optional[str]] = mapped_column(String(32), default=None)        # rss | newsapi | gdelt | extension
    source_provider: Mapped[Optional[str]] = mapped_column(String(255), default=None)   # feed name / "NewsAPI" / "GDELT"
    external_id: Mapped[Optional[str]] = mapped_column(String(512), default=None)       # provider's article id, if any
    # Content lifecycle (additive, generic so it can grow: provisional | verified | archived …).
    # ``None`` = the default active state (every feed-produced article, and all legacy rows);
    # ``"provisional"`` = created from a single user's extension read — participates in Stories/
    # Search/the corpus, but hidden from Discover until promoted; ``"verified"`` = an explicitly
    # promoted article (a feed re-discovered it, or enough distinct readers corroborated it), which
    # also records that the promotion happened. Derived from provenance at insert.
    article_state: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    fetched_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class FeedHealth(Base):
    """Per-feed polling health + quality — **observational only**. Written each poll cycle by the
    poller; never removes articles, never modifies :class:`FeedArticle`, and never influences corpus
    construction, export, or recommendation serving. A future Corpus Validation milestone may read it.
    Keyed by feed URL; no foreign key, so it is fully isolated from users/reads/reports/rec-events."""

    __tablename__ = "feed_health"

    feed_url: Mapped[str] = mapped_column(String(2048), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), default=None)          # publisher hint
    healthy: Mapped[bool] = mapped_column(default=True)
    consecutive_failures: Mapped[int] = mapped_column(default=0)
    total_polls: Mapped[int] = mapped_column(default=0)
    total_ok: Mapped[int] = mapped_column(default=0)
    total_failed: Mapped[int] = mapped_column(default=0)
    last_success_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    last_failure_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    last_error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    last_latency_ms: Mapped[Optional[float]] = mapped_column(default=None)
    avg_latency_ms: Mapped[Optional[float]] = mapped_column(default=None)
    newest_published: Mapped[Optional[str]] = mapped_column(String(64), default=None)   # last cycle
    oldest_published: Mapped[Optional[str]] = mapped_column(String(64), default=None)   # last cycle
    imported: Mapped[int] = mapped_column(default=0)          # new rows, last cycle
    duplicate: Mapped[int] = mapped_column(default=0)         # already-seen, last cycle
    rejected: Mapped[int] = mapped_column(default=0)          # failed URL/host guard, last cycle
    missing_metadata: Mapped[int] = mapped_column(default=0)  # no title / no pub date, last cycle
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


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
        self._ensure_media_columns()
        self._ensure_source_columns()
        self._ensure_read_columns()
        self._ensure_lifecycle_columns()
        self._ensure_search_indexes()

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
        payload = _dumps_scored(scored)
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
                            scored: dict, image: "str | None" = None,
                            image_width: "int | None" = None, image_height: "int | None" = None,
                            image_mime: "str | None" = None, image_source: "str | None" = None,
                            image_attribution: "str | None" = None, source_type: "str | None" = None,
                            source_provider: "str | None" = None, external_id: "str | None" = None) -> bool:
        """Insert a catalog article, or refresh an existing one (dedup by ``canonical_url``). Returns
        ``True`` when newly created, ``False`` on a re-poll. A re-poll refreshes ``fetched_at`` and
        backfills any field that was empty before, but never rewrites first-seen metadata — so the same
        canonical URL arriving from a **different source** merges into the one row (never duplicates).

        Media merge is **source-priority-aware**: an incoming image replaces the stored one when the row
        has none, or when the incoming ``source_type`` outranks the source of the **currently stored
        image** (via ``SOURCE_PRIORITY``); equal/lower priority keeps the existing image. The stored
        image's source is read from ``image_source`` (refreshed on every replace), so an upgraded image
        is compared against its real source, not the article's origin. Nothing extra is persisted.
        Callers that pass no ``source_type`` (priority 0) get the original backfill-when-empty behaviour,
        so existing callers are unchanged.

        Content lifecycle (Commit 18): ``article_state`` is derived from provenance — an article created
        by a user's browser extension is born ``"provisional"`` (hidden from Discover until promoted);
        every feed-produced article is born active (``None``). When a real feed later re-discovers a
        provisional article, the merge itself **promotes** it to ``"verified"`` — corroboration by an
        independent source is exactly the promotion signal the lifecycle wants."""
        payload = _dumps_scored(scored)
        with self.session() as s:
            row = s.get(FeedArticle, canonical_url)
            if row is None:
                s.add(FeedArticle(
                    canonical_url=canonical_url, url=url, publisher=publisher,
                    source_publisher=source_publisher, title=title, description=description,
                    body=body, published_at=published_at, source_feed=source_feed, scored=payload,
                    image=image, image_width=image_width, image_height=image_height,
                    image_mime=image_mime, image_source=image_source,
                    image_attribution=image_attribution, source_type=source_type,
                    source_provider=source_provider, external_id=external_id,
                    article_state=("provisional" if (source_type or "").lower() == "extension" else None)))
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
            if external_id and not row.external_id:
                row.external_id = external_id
            # Lifecycle promotion on merge: an independent feed source re-discovering a provisional
            # (extension-created) article corroborates it — promoted to an explicit "verified" state
            # (distinguishable from feed-born NULL, so the promotion itself stays auditable).
            if row.article_state == "provisional" and source_type and (source_type or "").lower() != "extension":
                row.article_state = "verified"
            # Media merge by source priority: fill when empty, else the higher-priority source's image
            # wins (equal/lower keeps the existing one). Precedence for the stored image comes from its
            # own source (``image_source``, refreshed on replace) — NOT the article's origin source_type.
            if image and (not row.image
                          or _media_priority(source_type)
                          > _stored_image_priority(row.image_source, row.source_type)):
                row.image, row.image_width, row.image_height = image, image_width, image_height
                row.image_mime, row.image_source, row.image_attribution = image_mime, image_source, image_attribution
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

    def delete_feed_articles(self, canonical_urls) -> int:
        """Delete FeedArticle rows by canonical URL (retention). Chunked to stay under SQLite's bound
        parameter limit. Returns the number deleted. Touches ONLY the ``feed_articles`` table — reads,
        report snapshots, analytics history, and rec-events are separate, user-keyed tables with no
        foreign key to ``feed_articles``, so they are never affected."""
        urls = [u for u in dict.fromkeys(canonical_urls) if u]   # de-dup, drop blanks, keep order
        if not urls:
            return 0
        deleted = 0
        with self.session() as s:
            for i in range(0, len(urls), 500):
                res = s.execute(delete(FeedArticle)
                                .where(FeedArticle.canonical_url.in_(urls[i:i + 500])))
                deleted += res.rowcount or 0
            s.commit()
        return deleted

    @staticmethod
    def _feed_row(r: "FeedArticle") -> dict:
        return {"canonicalUrl": r.canonical_url, "url": r.url, "publisher": r.publisher,
                "sourcePublisher": r.source_publisher, "title": r.title,
                "description": r.description, "body": r.body, "publishedAt": r.published_at,
                "sourceFeed": r.source_feed, "scored": dict(json.loads(r.scored)),
                "image": r.image, "imageWidth": r.image_width, "imageHeight": r.image_height,
                "imageMimeType": r.image_mime, "imageSource": r.image_source,
                "imageAttribution": r.image_attribution,
                "sourceType": r.source_type, "sourceProvider": r.source_provider,
                "externalId": r.external_id, "articleState": r.article_state,
                "fetchedAt": r.fetched_at.isoformat() if r.fetched_at else None,
                "createdAt": r.created_at.isoformat() if r.created_at else None}

    def feed_article_media(self, canonical_urls) -> dict:
        """Media + publication metadata for a set of catalog articles, keyed by canonical URL — the
        batched lookup the API layer uses to enrich already-serialised articles (whose corpus carries
        neither media nor timestamps) at serialization time. Every matched row returns its REAL
        ``publishedAt`` (the article's publication timestamp, else ``fetchedAt`` — the observed
        discovery time, same fallback Discover uses; never fabricated); image keys are present only
        when the row carries an image."""
        urls = [u for u in dict.fromkeys(canonical_urls) if u]
        if not urls:
            return {}
        out: dict = {}
        with self.session() as s:
            for i in range(0, len(urls), 500):
                rows = s.scalars(select(FeedArticle)
                                 .where(FeedArticle.canonical_url.in_(urls[i:i + 500]))).all()
                for r in rows:
                    rec = {"publishedAt": r.published_at
                           or (r.fetched_at.isoformat() if r.fetched_at else None)}
                    if r.image:
                        rec.update({
                            "image": r.image, "imageWidth": r.image_width, "imageHeight": r.image_height,
                            "imageMimeType": r.image_mime, "imageSource": r.image_source,
                            "imageAttribution": r.image_attribution})
                    out[r.canonical_url] = rec
        return out

    def _ensure_media_columns(self) -> None:
        """Additive, idempotent media columns on ``feed_articles`` — upgrades pre-existing DBs in place
        (``create_all`` only creates NEW tables). SQLite ``ADD COLUMN`` is cheap; a duplicate-column
        error (already present, e.g. a fresh DB) is ignored, so this is safe on every startup."""
        cols = [("image", "VARCHAR(2048)"), ("image_width", "INTEGER"), ("image_height", "INTEGER"),
                ("image_mime", "VARCHAR(128)"), ("image_source", "VARCHAR(255)"),
                ("image_attribution", "VARCHAR(512)")]
        for name, decl in cols:
            try:
                with self.session() as s:
                    s.execute(text(f"ALTER TABLE feed_articles ADD COLUMN {name} {decl}"))
            except Exception:
                pass    # already exists (fresh DB) or a non-sqlite backend — nothing to do

    def _ensure_source_columns(self) -> None:
        """Additive, idempotent source-attribution columns on ``feed_articles`` (upgrades pre-existing
        DBs in place, exactly like ``_ensure_media_columns``). Backward compatible — legacy rows keep
        ``NULL`` for these until a source sets them, and every existing API keeps working unchanged."""
        cols = [("source_type", "VARCHAR(32)"), ("source_provider", "VARCHAR(255)"),
                ("external_id", "VARCHAR(512)")]
        for name, decl in cols:
            try:
                with self.session() as s:
                    s.execute(text(f"ALTER TABLE feed_articles ADD COLUMN {name} {decl}"))
            except Exception:
                pass    # already exists (fresh DB) or a non-sqlite backend — nothing to do

    def _ensure_read_columns(self) -> None:
        """Additive, idempotent read-source columns on ``reads`` (Commit 14), upgrading pre-existing
        DBs in place exactly like ``_ensure_source_columns``. Backward compatible — legacy reads and
        the browser extension keep ``NULL`` here, and every read consumer keeps working unchanged."""
        cols = [("read_source", "VARCHAR(32)"), ("opened_from", "VARCHAR(64)"), ("device", "VARCHAR(64)")]
        for name, decl in cols:
            try:
                with self.session() as s:
                    s.execute(text(f"ALTER TABLE reads ADD COLUMN {name} {decl}"))
            except Exception:
                pass    # already exists (fresh DB) or a non-sqlite backend — nothing to do

    def _ensure_lifecycle_columns(self) -> None:
        """Additive, idempotent content-lifecycle column on ``feed_articles`` (Commit 18), upgrading
        pre-existing DBs in place exactly like the other ``_ensure_*`` migrations. Backward compatible —
        legacy rows keep ``NULL`` (= active), so nothing changes for feed-produced articles. Also
        carries over values from the short-lived ``status`` spelling of this column, so a DB created
        by the first cut of Commit 18 keeps its provisional flags."""
        try:
            with self.session() as s:
                s.execute(text("ALTER TABLE feed_articles ADD COLUMN article_state VARCHAR(16)"))
        except Exception:
            pass        # already exists (fresh DB) or a non-sqlite backend — nothing to do
        try:
            with self.session() as s:
                s.execute(text("UPDATE feed_articles SET article_state = status "
                               "WHERE article_state IS NULL AND status IS NOT NULL"))
        except Exception:
            pass        # no legacy 'status' column — the common case

    # -- catalog search (live, index-backed; never touches the recommender) ------------------------
    def _ensure_search_indexes(self) -> None:
        """Additive, idempotent search indexes on ``feed_articles`` (also upgrades pre-existing DBs).
        Column filters + sort become index-backed; the JSON ``lean``/``category`` get expression
        indexes (SQLite only). Purely additive — indexes never change results, only speed — and a
        failure here never blocks startup (search still works, just with a scan)."""
        stmts = ["CREATE INDEX IF NOT EXISTS ix_feed_publisher ON feed_articles(publisher)",
                 "CREATE INDEX IF NOT EXISTS ix_feed_published_at ON feed_articles(published_at)",
                 "CREATE INDEX IF NOT EXISTS ix_feed_source_feed ON feed_articles(source_feed)"]
        if self.engine.dialect.name == "sqlite":
            stmts += ["CREATE INDEX IF NOT EXISTS ix_feed_lean ON feed_articles(json_extract(scored,'$.lean'))",
                      "CREATE INDEX IF NOT EXISTS ix_feed_category ON feed_articles(json_extract(scored,'$.category'))"]
        try:
            with self.session() as s:
                for stmt in stmts:
                    s.execute(text(stmt))
        except Exception:
            pass

    @staticmethod
    def _lean_expr():
        return func.json_extract(FeedArticle.scored, "$.lean")

    @staticmethod
    def _category_expr():
        return func.json_extract(FeedArticle.scored, "$.category")

    def _search_conditions(self, *, q, publisher, lean, topic, date_from, date_to, source) -> list:
        """The WHERE terms for a catalog search — text (title/description/publisher/category), exact
        publisher/topic/source, lean bucket (via the JSON lean), and an ISO date range."""
        conds: list = []
        if q and q.strip():
            like = f"%{q.strip()}%"
            conds.append(or_(FeedArticle.title.ilike(like), FeedArticle.description.ilike(like),
                             FeedArticle.publisher.ilike(like), self._category_expr().ilike(like)))
        if publisher and publisher.strip():
            conds.append(func.lower(FeedArticle.publisher) == publisher.strip().lower())
        if source and source.strip():
            conds.append(FeedArticle.source_feed == source.strip())
        if topic and topic.strip():
            conds.append(func.lower(self._category_expr()) == topic.strip().lower())
        if lean == "left":
            conds.append(self._lean_expr() <= -0.5)
        elif lean == "right":
            conds.append(self._lean_expr() >= 0.5)
        elif lean == "center":
            conds.append(and_(self._lean_expr() > -0.5, self._lean_expr() < 0.5))
        if date_from and date_from.strip():
            conds.append(FeedArticle.published_at >= date_from.strip())
        if date_to and date_to.strip():
            conds.append(FeedArticle.published_at <= date_to.strip())
        return conds

    @staticmethod
    def _search_order(sort: str):
        if sort == "oldest":
            return (FeedArticle.published_at.asc(), FeedArticle.canonical_url.asc())
        if sort == "publisher":
            return (func.lower(FeedArticle.publisher).asc(), FeedArticle.published_at.desc())
        # "newest" (default) and "relevance" (future) -> newest publication first
        return (FeedArticle.published_at.desc(), FeedArticle.canonical_url.desc())

    def search_feed_articles(self, *, q=None, publisher=None, lean=None, topic=None,
                             date_from=None, date_to=None, source=None, sort="newest",
                             pagination=None, include_provisional: bool = True):
        """Search the catalog directly, in SQL. Returns ``(rows, total)`` — ``rows`` are paginated
        FeedArticle-row dicts, ``total`` the match count before pagination. All filtering / sorting /
        paging happen in the database (index-backed); it never touches the recommendation engine.
        ``pagination`` is a :class:`pagination.Pagination` (defaults to offset paging).
        ``include_provisional=False`` (the Discover surface only) hides extension-created articles that
        haven't been promoted yet; Search/Stories/export keep the default and see everything."""
        from pagination import OffsetPagination
        pg = pagination or OffsetPagination()
        conds = self._search_conditions(q=q, publisher=publisher, lean=lean, topic=topic,
                                         date_from=date_from, date_to=date_to, source=source)
        if not include_provisional:
            conds = list(conds) + [or_(FeedArticle.article_state.is_(None),
                                       FeedArticle.article_state != "provisional")]
        where = and_(*conds) if conds else None
        with self.session() as s:
            cnt = select(func.count()).select_from(FeedArticle)
            if where is not None:
                cnt = cnt.where(where)
            total = int(s.scalar(cnt) or 0)
            stmt = select(FeedArticle)
            if where is not None:
                stmt = stmt.where(where)
            stmt = pg.apply(stmt.order_by(*self._search_order(sort)))
            return [self._feed_row(r) for r in s.scalars(stmt).all()], total

    def feed_article_facets(self, include_provisional: bool = True) -> dict:
        """Distinct publishers + topics (categories) across the catalog, for filter dropdowns.
        ``include_provisional=False`` (Discover) keeps the facet counts consistent with what that
        surface actually lists — unpromoted extension-created articles are excluded."""
        cond = or_(FeedArticle.article_state.is_(None), FeedArticle.article_state != "provisional")
        with self.session() as s:
            pq, cq = select(FeedArticle.publisher).distinct(), select(self._category_expr()).distinct()
            if not include_provisional:
                pq, cq = pq.where(cond), cq.where(cond)
            pubs = [p for (p,) in s.execute(pq).all() if p]
            cats = [c for (c,) in s.execute(cq).all() if c]
        return {"publishers": sorted(set(pubs)), "topics": sorted(set(cats))}

    # -- content lifecycle (Commit 18: extension-created articles) --------------------
    def maybe_promote_feed_article(self, canonical_url: str, min_readers: int) -> bool:
        """Promote a ``provisional`` article to active once ``min_readers`` **distinct** users have
        read it — independent readers corroborate an extension-discovered article the way a feed
        re-discovery does. Idempotent and cheap: a no-op unless the row exists and is provisional.
        Returns ``True`` only when this call performed the promotion."""
        with self.session() as s:
            row = s.get(FeedArticle, canonical_url)
            if row is None or row.article_state != "provisional":
                return False
            readers = int(s.scalar(select(func.count(func.distinct(Read.user_id)))
                                   .where(Read.canonical_url == canonical_url)) or 0)
            if readers < max(1, int(min_readers)):
                return False
            row.article_state = "verified"
            return True

    def distinct_read_urls(self) -> set:
        """Every canonical URL any user has read — the read-demand set the corpus export keeps
        cap-exempt (an article someone actually read must stay in the recommendation corpus)."""
        with self.session() as s:
            return {u for (u,) in s.execute(select(Read.canonical_url).distinct()).all() if u}

    def feed_articles_by_urls(self, canonical_urls) -> list:
        """Catalog rows for specific canonical URLs (chunked ``IN``), in ``_feed_row`` shape —
        the fetch behind the read-demand export exemption."""
        urls = [u for u in dict.fromkeys(canonical_urls) if u]
        out = []
        with self.session() as s:
            for i in range(0, len(urls), 500):
                rows = s.scalars(select(FeedArticle)
                                 .where(FeedArticle.canonical_url.in_(urls[i:i + 500]))).all()
                out.extend(self._feed_row(r) for r in rows)
        return out

    def fts5_available(self) -> bool:
        """Whether this SQLite build has FTS5 compiled in — **diagnostics only** (FTS is not used yet;
        search is LIKE-based). Non-SQLite backends report False."""
        if self.engine.dialect.name != "sqlite":
            return False
        try:
            with self.session() as s:
                return bool(s.scalar(text("SELECT sqlite_compileoption_used('ENABLE_FTS5')")))
        except Exception:
            return False

    # -- per-feed health (observational only; never affects articles/corpus/recs) --------
    def record_feed_health(self, feed_url: str, *, ok: bool, name: "str | None" = None,
                           latency_ms: "float | None" = None, error=None, stats: "dict | None" = None,
                           unhealthy_after: int = 3, at: "str | None" = None) -> dict:
        """Upsert one poll result into ``feed_health``. Success resets the consecutive-failure
        counter; failure increments it and marks the feed unhealthy at ``unhealthy_after``. Maintains
        cumulative counters + a running-average latency. Observational — writes only ``feed_health``,
        and returns the updated record as a dict."""
        now = _utcnow()
        when = at or now.isoformat()
        stats = stats or {}
        with self.session() as s:
            row = s.get(FeedHealth, feed_url)
            existed = row is not None
            prev_healthy = row.healthy if existed else True
            if not existed:
                # explicit zeros: mapped_column(default=…) only applies at flush, but we mutate first
                row = FeedHealth(feed_url=feed_url, healthy=True, consecutive_failures=0,
                                 total_polls=0, total_ok=0, total_failed=0,
                                 imported=0, duplicate=0, rejected=0, missing_metadata=0)
                s.add(row)
            if name:
                row.name = name
            row.total_polls += 1
            if latency_ms is not None:
                row.last_latency_ms = float(latency_ms)
                prev = row.avg_latency_ms if row.avg_latency_ms is not None else float(latency_ms)
                row.avg_latency_ms = prev + (float(latency_ms) - prev) / row.total_polls
            if ok:
                row.total_ok += 1
                row.consecutive_failures = 0
                row.last_success_at = when
                row.last_error = None
                row.imported = int(stats.get("imported", stats.get("new", 0)))
                row.duplicate = int(stats.get("duplicate", stats.get("duplicates", 0)))
                row.rejected = int(stats.get("rejected", stats.get("skipped", 0)))
                row.missing_metadata = int(stats.get("missing_metadata", 0))
                row.newest_published = stats.get("newest")
                row.oldest_published = stats.get("oldest")
            else:
                row.total_failed += 1
                row.consecutive_failures += 1
                row.last_failure_at = when
                row.last_error = (str(error)[:1000] if error is not None else "unknown")
            row.healthy = row.consecutive_failures < int(unhealthy_after)
            row.updated_at = now
            transition = None                       # health-state change, for the poller to log
            if existed and prev_healthy and not row.healthy:
                transition = "unhealthy"
            elif existed and not prev_healthy and row.healthy:
                transition = "recovered"
            rec = self._feed_health_row(row)
            rec["transition"] = transition
            return rec

    def list_feed_health(self) -> list:
        with self.session() as s:
            rows = s.scalars(select(FeedHealth).order_by(FeedHealth.feed_url)).all()
            return [self._feed_health_row(r) for r in rows]

    @staticmethod
    def _feed_health_row(r: "FeedHealth") -> dict:
        return {"feedUrl": r.feed_url, "name": r.name, "healthy": r.healthy,
                "consecutiveFailures": r.consecutive_failures, "totalPolls": r.total_polls,
                "totalOk": r.total_ok, "totalFailed": r.total_failed,
                "lastSuccessAt": r.last_success_at, "lastFailureAt": r.last_failure_at,
                "lastError": r.last_error, "lastLatencyMs": r.last_latency_ms,
                "avgLatencyMs": round(r.avg_latency_ms, 1) if r.avg_latency_ms is not None else None,
                "newestPublished": r.newest_published, "oldestPublished": r.oldest_published,
                "imported": r.imported, "duplicate": r.duplicate, "rejected": r.rejected,
                "missingMetadata": r.missing_metadata,
                "updatedAt": r.updated_at.isoformat() if r.updated_at else None}

    # -- reading events (idempotent per user + canonical URL) -----------
    def add_read(self, user_id: int, canonical_url: str, scored: dict,
                 observed_at: "str | None" = None, *, read_source: "str | None" = None,
                 opened_from: "str | None" = None, device: "str | None" = None) -> bool:
        """Record a reading event; return ``True`` if new, ``False`` if this (user, url) was
        already read — idempotent, no duplicate row. ``read_source`` / ``opened_from`` / ``device``
        are additive attribution metadata (Commit 14): stored on the new row, never consulted for
        dedup and never re-scored, so any source (app, extension, future import) shares this one path."""
        with self.session() as s:
            exists = s.scalar(select(Read.id).where(Read.user_id == user_id,
                                                    Read.canonical_url == canonical_url))
            if exists is not None:
                return False
            s.add(Read(user_id=user_id, canonical_url=canonical_url,
                       scored=_dumps_scored(scored), observed_at=observed_at,
                       read_source=read_source, opened_from=opened_from, device=device))
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
                     "readSource": r.read_source, "openedFrom": r.opened_from, "device": r.device,
                     "createdAt": r.created_at.isoformat() if r.created_at else None}
                    for r in rows]

    def count_reads(self, user_id: int) -> int:
        """How many distinct articles the user has read."""
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(Read)
                                .where(Read.user_id == user_id)) or 0)

    # -- saved articles (the single "Saved" concept; no separate bookmark) ----
    def save_article(self, user_id: int, article_id: str, article: dict) -> bool:
        """Persist a saved article for a user. Idempotent per ``(user, article)``: saving one that is
        already saved is a no-op that only refreshes the stored snapshot (the duplicate is ignored).
        Returns ``True`` when this save newly created the row, ``False`` when it already existed."""
        aid = str(article_id)
        payload = json.dumps(article or {})
        with self.session() as s:
            row = s.scalar(select(SavedArticle).where(SavedArticle.user_id == user_id,
                                                      SavedArticle.article_id == aid))
            if row is None:
                s.add(SavedArticle(user_id=user_id, article_id=aid, article=payload))
                return True
            row.article = payload                       # keep the snapshot fresh; still one saved row
            return False

    def unsave_article(self, user_id: int, article_id: str) -> bool:
        """Remove a user's saved article. Returns ``True`` when a row was deleted, ``False`` when the
        article was not saved (so unsaving twice, or unsaving something never saved, is safe)."""
        aid = str(article_id)
        with self.session() as s:
            row = s.scalar(select(SavedArticle).where(SavedArticle.user_id == user_id,
                                                      SavedArticle.article_id == aid))
            if row is None:
                return False
            s.delete(row)
            return True

    def list_saved(self, user_id: int) -> list:
        """A user's saved articles, newest-first — each ``{articleId, article, savedAt}`` with the
        stored Article snapshot parsed back to a dict."""
        with self.session() as s:
            rows = s.scalars(select(SavedArticle).where(SavedArticle.user_id == user_id)
                             .order_by(SavedArticle.created_at.desc(), SavedArticle.id.desc())).all()
            return [{"articleId": r.article_id, "article": json.loads(r.article),
                     "savedAt": r.created_at.isoformat() if r.created_at else None} for r in rows]

    def count_saved(self, user_id: int) -> int:
        """How many articles the user has saved — the real number behind the profile's Saved counter."""
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(SavedArticle)
                                .where(SavedArticle.user_id == user_id)) or 0)

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

    def count_unopened_recommendations(self, user_id: int, since: "str | None" = None) -> int:
        """How many recommendations were **surfaced but not opened** — ``RecEvent`` rows with no
        ``opened_at`` (optionally restricted to those surfaced at/after ``since``, an ISO string). A
        pure count over reception events the serving path already recorded: **no recommender is
        invoked, nothing is ranked, and no feed is generated.**"""
        with self.session() as s:
            q = select(func.count()).select_from(RecEvent).where(
                RecEvent.user_id == user_id, RecEvent.opened_at.is_(None))
            if since is not None:
                q = q.where(RecEvent.shown_at >= since)
            return int(s.scalar(q) or 0)

    # -- recommendation feedback (explicit like/dislike/ignore/read_later) ----
    def record_recommendation_feedback(self, user_id: int, article_id: str, feedback: str,
                                       at: "str | None" = None) -> bool:
        """Persist one explicit feedback signal on a recommendation (``like`` / ``dislike`` /
        ``ignore`` / ``read_later``). Idempotent per ``(user, article, feedback)``: repeating the same
        signal refreshes ``updated_at`` and returns ``False``; a new signal creates a row and returns
        ``True``. **Records only** — nothing here is read by any recommender, ranking, report, or
        personalization path (B1). Raises ``ValueError`` for an unknown feedback type (the API layer
        also rejects it at the edge, so this is defence in depth)."""
        if feedback not in RECOMMENDATION_FEEDBACK_TYPES:
            raise ValueError(f"unknown recommendation feedback type: {feedback!r}")
        stamp = at or _utcnow().isoformat()
        aid = str(article_id)
        with self.session() as s:
            row = s.scalar(select(RecFeedback).where(RecFeedback.user_id == user_id,
                                                     RecFeedback.article_id == aid,
                                                     RecFeedback.feedback == feedback))
            if row is None:
                s.add(RecFeedback(user_id=user_id, article_id=aid, feedback=feedback,
                                  created_at=stamp, updated_at=stamp))
                return True
            row.updated_at = stamp
            return False

    def list_recommendation_feedback(self, user_id: int) -> list:
        """All of a user's recommendation feedback, oldest-first: ``{articleId, feedback, createdAt,
        updatedAt}``. A read-only projection for the web tier (e.g. to keep an *ignored* card
        dismissed across a reload); it drives no ranking and invokes no recommender."""
        with self.session() as s:
            rows = s.scalars(select(RecFeedback).where(RecFeedback.user_id == user_id)
                             .order_by(RecFeedback.id)).all()
            return [{"articleId": r.article_id, "feedback": r.feedback,
                     "createdAt": r.created_at, "updatedAt": r.updated_at} for r in rows]

    # -- improvement-recommendation lifecycle ledger (RC2.3) ------------
    def list_improvement_lifecycle(self, user_id: int) -> list:
        """Every improvement-recommendation lifecycle row for a user (one per ``rec_key``), oldest
        first, as camelCase dicts — the shape :mod:`improvement_ledger` reconciles over and the web
        tier reads. Read-only; drives no recommender, ranking, selection, or report computation."""
        with self.session() as s:
            rows = s.scalars(select(ImprovementLifecycle)
                             .where(ImprovementLifecycle.user_id == user_id)
                             .order_by(ImprovementLifecycle.id)).all()
            return [_improvement_lifecycle_to_dict(r) for r in rows]

    def save_improvement_lifecycle(self, user_id: int, rows: "list[dict]") -> int:
        """Upsert reconciled lifecycle rows for a user (idempotent per ``rec_key``). Scalar fields
        (metric/state/scores) are authoritative from the reconciler; timestamp columns are only ever
        *set* — a stamped transition time is never cleared — so the history stays reconstructable.
        Returns the number of rows written."""
        stamp = _utcnow().isoformat()
        written = 0
        with self.session() as s:
            for row in rows:
                rk = str(row["recKey"])
                r = s.scalar(select(ImprovementLifecycle).where(
                    ImprovementLifecycle.user_id == user_id, ImprovementLifecycle.rec_key == rk))
                if r is None:
                    r = ImprovementLifecycle(user_id=user_id, rec_key=rk,
                                             metric=str(row.get("metric") or ""),
                                             state=str(row.get("state") or "generated"))
                    s.add(r)
                r.metric = str(row.get("metric") or r.metric)
                if row.get("state") is not None:
                    r.state = str(row["state"])
                if row.get("firstScore") is not None and r.first_score is None:
                    r.first_score = int(row["firstScore"])
                if row.get("currentScore") is not None:
                    r.current_score = int(row["currentScore"])
                if row.get("completedScore") is not None and r.completed_score is None:
                    r.completed_score = int(row["completedScore"])
                for cam, col in (("generatedAt", "generated_at"), ("shownAt", "shown_at"),
                                 ("viewedAt", "viewed_at"), ("acceptedAt", "accepted_at"),
                                 ("dismissedAt", "dismissed_at"), ("completedAt", "completed_at"),
                                 ("expiredAt", "expired_at"), ("supersededAt", "superseded_at")):
                    val = row.get(cam)
                    # shown_at legitimately refreshes each serve; every other stamp is set-once.
                    if val is not None and (col == "shown_at" or getattr(r, col) is None):
                        setattr(r, col, val)
                if row.get("supersededBy") is not None and r.superseded_by is None:
                    r.superseded_by = str(row["supersededBy"])
                r.updated_at = stamp
                written += 1
        return written

    def record_improvement_lifecycle_event(self, user_id: int, rec_key: str, metric: str,
                                           event: str, at: "str | None" = None) -> bool:
        """Record one explicit reader lifecycle signal — ``accepted`` / ``dismissed`` / ``viewed`` — on
        an improvement recommendation, creating the ledger row if this is the first time it is seen.
        Idempotent: re-sending the same event refreshes its timestamp. Returns ``True`` when the row was
        newly created. The derived states (completed/expired/superseded/in_progress) are owned by the
        reconciler, not this method — here we only stamp the reader's own signal and reflect it in the
        state (unless the row is already completed)."""
        col = IMPROVEMENT_LIFECYCLE_EVENTS.get(event)
        if col is None:
            raise ValueError(f"unknown improvement lifecycle event: {event!r}")
        stamp = at or _utcnow().isoformat()
        with self.session() as s:
            r = s.scalar(select(ImprovementLifecycle).where(
                ImprovementLifecycle.user_id == user_id, ImprovementLifecycle.rec_key == str(rec_key)))
            created = r is None
            if created:
                r = ImprovementLifecycle(user_id=user_id, rec_key=str(rec_key),
                                         metric=str(metric or ""), state="generated",
                                         generated_at=stamp)
                s.add(r)
            setattr(r, col, stamp)
            if metric and not r.metric:
                r.metric = str(metric)
            if r.state != "completed":
                r.state = event                          # accepted | dismissed | viewed
            r.updated_at = stamp
            return created

    # -- notifications (delivery-boundary persistence) ------------------
    def record_notifications(self, user_id: int, notifications: "list[dict]") -> int:
        """Persist due notifications for a user — the materialisation primitive behind the delivery
        boundary. Each item is a JSON-safe notification dict (a ``notification_service.Notification``
        as ``dataclasses.asdict``); this store never imports that module — it only writes dicts, the
        same way :meth:`save_report` / :meth:`save_settings` do. Idempotent per
        ``(user_id, kind, dedupe_key)``: a notification already recorded (this batch or an earlier
        one) is skipped, never duplicated and never overwritten. Concurrency-safe: the DB-level
        ``UNIQUE(user_id, kind, dedupe_key)`` constraint is the source of truth, so if a concurrent
        request materialises the same notification first, our losing insert is caught and skipped
        rather than failing the whole call. Returns how many rows were newly created."""
        new = 0
        seen: set = set()                       # in-batch guard (independent of autoflush)
        with self.session() as s:
            for n in notifications:
                kind = str(n.get("kind") or "")
                dedupe = str(n.get("dedupe_key") or "")
                if (kind, dedupe) in seen:
                    continue
                seen.add((kind, dedupe))
                exists = s.scalar(select(Notification.id).where(
                    Notification.user_id == user_id, Notification.kind == kind,
                    Notification.dedupe_key == dedupe))
                if exists is not None:
                    continue                    # already delivered -> idempotent skip
                try:
                    # Savepoint per insert so a UNIQUE violation isolates to *this* row: a concurrent
                    # request may have inserted the same (user_id, kind, dedupe_key) between the
                    # SELECT above and this flush. The DB constraint is the arbiter; the loser's
                    # savepoint rolls back while rows already added in this batch survive.
                    with s.begin_nested():
                        s.add(Notification(
                            user_id=user_id, kind=kind, dedupe_key=dedupe,
                            body=json.dumps(_json_safe(n)),
                            created_at=str(n.get("created_at") or _utcnow().isoformat())))
                        s.flush()
                    new += 1
                except IntegrityError:
                    continue                    # concurrent writer won -> idempotent skip
        return new

    def list_notifications(self, user_id: int, *, unseen_only: bool = False,
                           limit: int = 50) -> "list[dict]":
        """A user's notifications, **newest-first**, capped at ``limit``. Each entry is the stored
        body (the notification dict) plus its persistent ``id`` and ``seenAt`` read-state.
        ``unseen_only`` restricts to notifications not yet marked seen. Read-only — no evaluation and
        no producers are touched. The persisted row metadata (``id`` / ``seenAt``) is spread last, so
        a payload that happens to carry those keys can never shadow the real row identity."""
        with self.session() as s:
            q = select(Notification).where(Notification.user_id == user_id)
            if unseen_only:
                q = q.where(Notification.seen_at.is_(None))
            rows = s.scalars(q.order_by(Notification.id.desc()).limit(limit)).all()
        out = []
        for r in rows:
            try:
                body = dict(json.loads(r.body))
            except (TypeError, ValueError):
                body = {}
            out.append({**body, "id": r.id, "seenAt": r.seen_at})
        return out

    def delivered_notification_keys(self, user_id: int) -> "set[str]":
        """The set of ``dedupe_key``s already delivered to a user — the idempotency **ledger** the
        delivery boundary reads to suppress re-delivery (it feeds ``notification_service``'s
        ``delivered_keys``). Returns the raw dedupe keys, matching what ``evaluate`` compares against."""
        with self.session() as s:
            rows = s.scalars(select(Notification.dedupe_key)
                             .where(Notification.user_id == user_id)).all()
        return set(rows)

    def mark_notification_seen(self, user_id: int, notification_id: int,
                              seen_at: "str | None" = None) -> bool:
        """Mark one of a user's notifications as seen — **idempotent** and **user-scoped**. Stamps
        ``seen_at`` on the row only if it belongs to ``user_id`` and isn't already seen. Returns
        ``True`` when this call changed the row (first time seen), and ``False`` when it was already
        seen or does not belong to this user (another user's id is never touched)."""
        stamp = seen_at or _utcnow().isoformat()
        with self.session() as s:
            row = s.scalar(select(Notification).where(Notification.id == notification_id,
                                                      Notification.user_id == user_id))
            if row is None or row.seen_at is not None:
                return False
            row.seen_at = stamp
            return True

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
