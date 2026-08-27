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

import gzip
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlsplit

from sqlalchemy import (ForeignKey, String, Text, UniqueConstraint, and_, create_engine,
                        delete, event, func, or_, select, text, update)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            relationship, sessionmaker)
from sqlalchemy.pool import StaticPool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_gap_days(earlier: "str | None", later: "str | None") -> "float | None":
    """Days between two ISO timestamps, or ``None`` when either is missing or unparseable.

    ``None`` rather than 0.0, so an absent previous evaluation reads as "no interval to judge"
    rather than as "zero days apart" — the latter would hold every first sample forever."""
    def _p(v):
        if not v:
            return None
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    a, b = _p(earlier), _p(later)
    return None if a is None or b is None else (b - a).total_seconds() / 86400.0


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
    already treats a missing value and ``NaN`` identically (``discover._num_or_none`` serialises
    both as null lean — L2.2; ``feed_source._bias_label`` drops the row on either). But ``json.dumps`` at its
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


def _url_host(url) -> str:
    """Bare lower-case host of an absolute URL ("https://www.NPR.org/x" -> "npr.org"); "" when
    the value has no host. Used for counted per-publisher host facts, never for identity."""
    try:
        host = urlsplit(str(url or "")).netloc.split("@")[-1].split(":", 1)[0].strip().lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _register_bucket(register) -> "str | None":
    """THE register bucketing for the feed/product layer — one implementation, so the Article
    serializer (``discover._register``), the publisher tone module, and every future consumer
    classify identically. Label strings pass through; a numeric P(reporting) uses the engine's
    own thresholds (>= 0.6 reporting, <= 0.4 opinion, else mixed — ``api_server._register_enum``'s
    numbers; that legacy helper also maps non-finite to "mixed" for the recommendation surface and
    is deliberately untouched). Absent / non-finite -> ``None`` — no signal, never a default."""
    if isinstance(register, str):
        r = register.strip().lower()
        return r if r in ("reporting", "opinion", "mixed") else None
    try:
        v = float(register)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return "reporting" if v >= 0.6 else ("opinion" if v <= 0.4 else "mixed")


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


class SourceLifecycle(Base):
    """Where one outlet sits in the source pipeline, and since when — M9, `docs/SCALE_ROADMAP.md`.

    **This table is not the tiering configuration and must never become it.** Tier membership is
    `RWE_CORPUS_TIER_B` / `RWE_CORPUS_SHADOW`, read from the environment; M9 emits config for a human
    to deploy and never mutates serving state. What lives here is the *record* — what state an outlet
    is in, since when, on what evidence — which is what makes a transition reversible and auditable.

    Two columns earn the table on their own:

    ``first_observed``  pinned on first sight and never moved forward. `observed_days` is derived
                        from ``MIN(created_at)``, which **retention erodes**: measured on production,
                        sportskeeda's first-seen advanced 50 minutes between two runs 18 minutes
                        apart while the global floor did not move at all (retention orders by
                        ``published_at``; observation reads ``created_at`` — different columns). An
                        outlet's observation window must not shrink because its oldest rows aged
                        out, so once seen, the date is kept here.
    ``streak``          consecutive evaluations agreeing on the same target. Hysteresis needs memory
                        across runs, and a run is a fresh process.
    """

    __tablename__ = "source_lifecycle"

    identity: Mapped[str] = mapped_column(String(255), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    since: Mapped[str] = mapped_column(String(64))              # ISO — entered `state`
    first_observed: Mapped[str] = mapped_column(String(64))     # ISO — pinned against retention
    last_seen: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    last_verdict: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    last_target: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    streak: Mapped[int] = mapped_column(default=0)
    last_evaluated_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    evidence: Mapped[str] = mapped_column(Text, default="{}")   # JSON snapshot of the last stats
    reason: Mapped[Optional[str]] = mapped_column(Text, default=None)


class SourceLifecycleEvent(Base):
    """An append-only record of every lifecycle transition — the ledger proper.

    ``SourceLifecycle`` holds the current state and is overwritten; this is never overwritten. The
    roadmap's Stage 6 is explicit about why: *"A retirement that deletes evidence cannot be audited
    later, and the recurring lesson in `PERFORMANCE.md` is that the expensive failures are the ones
    where the evidence was gone."* A row here carries the evidence snapshot that justified the move,
    so a decision can be re-read years later against the numbers it was actually made on — not
    against today's, which will have changed."""

    __tablename__ = "source_lifecycle_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    identity: Mapped[str] = mapped_column(String(255), index=True)
    frm: Mapped[str] = mapped_column(String(16))
    to: Mapped[str] = mapped_column(String(16))
    at: Mapped[str] = mapped_column(String(64))
    automatic: Mapped[bool] = mapped_column(default=False)
    applied: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, default=None)
    evidence: Mapped[str] = mapped_column(Text, default="{}")


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
# the web tier maps its own hyphen forms ("read-later", "fewer-from-source", …) onto these before
# calling. The last five are the Tier-2 vocabulary (docs/X_ALGORITHM_AUDIT_AND_PROPOSAL.md,
# Phase 13.6): finer-grained than like/dislike so the ranking consequence can match the reader's
# actual complaint — "fewer from this SOURCE" dims the publisher without smearing the topic,
# "more of this TOPIC" lifts the topic without boosting one outlet, and the three article-scoped
# ones (another_viewpoint / already_know / too_repetitive) say why a card was unwanted.
RECOMMENDATION_FEEDBACK_TYPES = ("like", "dislike", "ignore", "read_later",
                                 "another_viewpoint", "already_know", "too_repetitive",
                                 "fewer_from_source", "more_topic")


class RecFeedback(Base):
    """A reader's explicit feedback on a recommendation the engine already produced — one of
    :data:`RECOMMENDATION_FEEDBACK_TYPES`. One row per ``(user_id, article_id, feedback)``:
    repeating the same signal is idempotent (refreshes ``updated_at``), while distinct feedback types
    on one article are distinct rows, so a reader's full set of signals is preserved without collapsing
    contradictory ones. Originally **recorded only** (B1); since Tier 1 the flag-gated
    ``rec_context`` reads it into ranking as bounded multipliers, and removal
    (:meth:`Store.remove_recommendation_feedback`) is the "undo" behind the visible-consequence UI —
    a reader can always see and retract what the feed holds against their name."""

    __tablename__ = "rec_feedback"
    __table_args__ = (UniqueConstraint("user_id", "article_id", "feedback",
                                       name="uq_recfeedback_user_article_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    article_id: Mapped[str] = mapped_column(String(2048))
    # one of RECOMMENDATION_FEEDBACK_TYPES. Declared width fits the longest ("another_viewpoint",
    # 17 chars); SQLite stores TEXT regardless, so widening from the original 16 needs no migration.
    feedback: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[str] = mapped_column(String(64))     # ISO — first time this signal was given
    updated_at: Mapped[str] = mapped_column(String(64))     # ISO — last time it was (re)submitted


class ExperimentAssignment(Base):
    """A reader's recorded arm in one recommendation experiment (Tier 2, Phase 13.4/13.9).

    The arm itself is DERIVED — ``rec_experiments.cohort_of`` is a pure hash, and this table is
    never consulted to decide anything — but analysis must not have to re-derive membership from
    a hash it hopes still matches the code that served the feeds. Write-once per
    ``(user_id, experiment)``: the first serve under a declared experiment records the arm, and
    re-serves are no-ops, so ``assigned_at`` is honestly "when this reader first entered the
    experiment"."""

    __tablename__ = "experiment_assignments"
    __table_args__ = (UniqueConstraint("user_id", "experiment",
                                       name="uq_experiment_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    experiment: Mapped[str] = mapped_column(String(64))
    cohort: Mapped[str] = mapped_column(String(16))         # treatment | control
    assigned_at: Mapped[str] = mapped_column(String(64))    # ISO


class EventVerdict(Base):
    """One judged (or queued) article pair for the banded event-identity mechanism
    (``event_identity``): the ambiguity-band pairs a story build wanted a semantic opinion on.

    A row is created PENDING (``verdict`` NULL) by the build's band emission, carrying SNAPSHOTS
    of both sides' headline/summary/date — so the judge decides exactly what the clusterer saw,
    even after the catalog rows rotate. The out-of-band worker fills ``verdict``. Keyed by
    ``event_identity.pair_key`` (order-independent, rubric-versioned): a rubric change mints new
    keys and old verdicts simply stop matching. ``source`` records how the verdict was reached —
    ``model`` rows are the only ones a build consults; ``api-error`` rows are retried by the
    worker after a cooldown and never influence clustering."""

    __tablename__ = "event_verdicts"

    pair_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    url_a: Mapped[str] = mapped_column(Text)
    url_b: Mapped[str] = mapped_column(Text)
    title_a: Mapped[str] = mapped_column(Text, default="")
    dek_a: Mapped[str] = mapped_column(Text, default="")
    published_a: Mapped[str] = mapped_column(String(64), default="")
    title_b: Mapped[str] = mapped_column(Text, default="")
    dek_b: Mapped[str] = mapped_column(Text, default="")
    published_b: Mapped[str] = mapped_column(String(64), default="")
    verdict: Mapped["str | None"] = mapped_column(String(24), nullable=True)  # NULL = pending
    source: Mapped[str] = mapped_column(String(24), default="")   # model | api-error
    model: Mapped[str] = mapped_column(String(64), default="")
    first_seen: Mapped[str] = mapped_column(String(64))           # ISO
    judged_at: Mapped["str | None"] = mapped_column(String(64), nullable=True)


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


class NotificationEvent(Base):
    """A **global**, user-agnostic occurrence worth telling readers about — the trigger side of the
    notification platform, and the counterpart to :class:`Notification` (which is per-reader).

    Every notification kind that exists today is derived from *one reader's own* persisted state:
    their report, their reads, their streak. The delivery boundary can therefore evaluate them on
    that reader's fetch and needs no global input. A breaking story is the first thing that is **not**
    like that — it happens once, to nobody in particular, and many readers should hear about it. This
    table is where that kind of fact lives.

    **Why a table and not a check at read time.** The signal it records is derived and *oscillates*:
    ``story_intelligence.compute_freshness`` returns the band ``"Breaking"`` from a rolling window, so
    a story crosses in, drops out as the window slides, and crosses in again on the next wave of
    coverage. A notification must fire on the **edge**, not the level. ``UNIQUE(source_type,
    source_id)`` is that edge: the first sighting creates the row, every later one is rejected, and
    the row outlives the condition. Nothing else in the system remembers "we already said this".

    ``payload`` is stored verbatim JSON, like ``notifications.body`` / ``report_snapshots`` — this
    table persists dicts and imports nothing from the notification modules. ``occurred_at`` is the
    event's own (injected) timestamp; ``expires_at`` is the staleness cutoff past which the event is
    no longer worth materialising for a reader who has not looked in a while (a three-day-old
    "breaking" is not breaking). ``category`` is the axis reader preferences gate on, and the reason
    this table is reusable: a product announcement or a digest cycle is the same shape of row.

    Product state only — no recommender, report, or corpus path reads it."""

    __tablename__ = "notification_events"
    __table_args__ = (UniqueConstraint("source_type", "source_id",
                                       name="uq_notification_event_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(48), index=True)   # "story_breaking"
    source_id: Mapped[str] = mapped_column(String(255))                # the story id
    category: Mapped[str] = mapped_column(String(32), index=True)      # "breaking" — what prefs gate
    payload: Mapped[str] = mapped_column(Text)                         # JSON, verbatim
    occurred_at: Mapped[str] = mapped_column(String(64), index=True)   # injected; the ordering key
    expires_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)   # None = never
    recorded_at: Mapped[datetime] = mapped_column(default=_utcnow)     # DB write time


class PushOwnershipError(Exception):
    """A push subscription was claimed for an account that cannot prove it holds the subscription.

    Raised only when an endpoint already registered to one reader is submitted by another WITHOUT the
    subscription's ``auth`` secret. The API turns it into a 409; nothing else in the store raises it,
    and no legitimate browser can provoke it (a subscription's endpoint and keys are minted together
    and rotate together, so the pair is either both current or both replaced)."""


class PushSubscription(Base):
    """One browser's Web Push subscription — a **device**, not a reader.

    A reader may hold several (laptop, phone, a second browser) and each carries its own endpoint and
    its own encryption keys, so this is the first per-user table whose grain is finer than the user.
    ``docs/BROWSER_PUSH_ARCHITECTURE.md`` §7 is the contract; this row is its storage.

    **``endpoint`` is the identity, and it is globally unique** — the push service mints it, and it
    already names exactly one browser instance. Keying on ``(user_id, endpoint)`` instead would let
    the same browser appear under two accounts, which is not a hypothetical: signing out and signing
    in as someone else on a shared machine re-subscribes the *same* endpoint. The push service would
    then deliver one message that two accounts believe is theirs. UNIQUE on ``endpoint`` alone makes
    the second subscription a REASSIGNMENT, which is what actually happened.

    **The four ``push_*`` columns are a denormalised copy of the reader's per-category push
    preferences**, and they exist for one reason: fan-out-on-write has to ask "which subscriptions
    want this category", and preferences live in an opaque JSON blob (``user_settings.value``) that
    cannot be indexed. They are a QUERY ACCELERATOR and never the authority — ``settings_service`` +
    ``notification_service.gate_path`` decide, and a stale copy here is corrected by that decision,
    never allowed to override it. Nothing reads them yet (delivery is Phase B2); they ship now for the
    same reason ``settings.notifications.categories.*.push`` shipped in Phase A — so the shape exists
    before the rows do, and adding the column later does not mean rewriting every reader's row.

    Product state only. Pruned by exactly one thing, and not by retention: a ``410 Gone`` from the
    push service, which means the browser revoked it (see ``retention_policy.PROTECTED_TABLES``)."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_push_subscription_endpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # The push service's delivery URL. Long by nature (FCM endpoints run ~200 chars, and the spec sets
    # no limit), so the column is generous and the API bounds what it will accept.
    endpoint: Mapped[str] = mapped_column(String(1024))
    # The subscription's public key and auth secret (base64url, from `PushSubscription.getKey`). The
    # payload is encrypted TO these, so they are not credentials of ours — they are the device's
    # address. Stored verbatim; never logged.
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    # Content encoding the browser negotiated (`aes128gcm` universally today). Recorded rather than
    # assumed so a future browser that offers something else is visible in the data, not a surprise
    # at send time.
    content_encoding: Mapped[str] = mapped_column(String(32), default="aes128gcm")
    # The browser's own expiry hint (`PushSubscription.expirationTime`), almost always null. Advisory:
    # a 410 is the authoritative signal, this is only an early warning.
    expires_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    # Which device this is, for the operator and for the reader's own "your devices" list. Truncated
    # by the API; carries no identifier we do not already have.
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    # Denormalised preference mirror — see the class docstring. Indexed because the fan-out query
    # filters on exactly one of them.
    push_breaking: Mapped[bool] = mapped_column(default=False, index=True)
    push_digests: Mapped[bool] = mapped_column(default=False, index=True)
    push_recommendations: Mapped[bool] = mapped_column(default=False, index=True)
    push_product: Mapped[bool] = mapped_column(default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    # Refreshed on every re-registration. A browser re-subscribes on its own schedule, so this is how
    # an operator tells a live device from one that has not checked in since it was set up.
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


class EmailSuppression(Base):
    """An address we must not write to again, and why.

    A hard bounce is not a transient failure; it is the receiving server stating that the mailbox
    does not exist. Sending to it again is what turns one typo into a sender reputation problem —
    and reputation is what decides whether the mail readers DO want arrives at all. So the outcome
    is recorded against the address, permanently, and consulted before every send.

    Keyed by ADDRESS rather than user id on purpose: the same mailbox can belong to more than one
    account, and a bounce is a fact about the mailbox. A reader who fixes their address is no
    longer suppressed, because the new address was never on this list."""

    __tablename__ = "email_suppressions"
    address: Mapped[str] = mapped_column(String(320), primary_key=True)   # RFC 5321 max
    reason: Mapped[str] = mapped_column(String(32))                       # bounced | complained
    detail: Mapped[str] = mapped_column(Text, default="")
    status_code: Mapped[Optional[int]] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class NotificationDelivery(Base):
    """One attempt to deliver one notification to one destination — the platform's **third** level of
    idempotency (``docs/NOTIFICATION_PLATFORM.md`` §3), and the only durable record that a send ever
    happened.

    ``UNIQUE(notification_id, channel, subscription_id)`` is what stops the same notification being
    pushed to the same device twice. It matters more here than anywhere else in the platform: the
    delivery worker runs on every poll cycle, so without it every cycle would re-send every
    still-unexpired notification to every device, forever.

    The row is **claimed before the send and resolved after it**, and since B3 the claim is a *lease*
    rather than a one-shot: a retryable outcome schedules another attempt (``next_attempt_at``), and a
    row left ``pending`` past :data:`push_retry.LEASE_SECONDS` is recoverable, because the only thing
    that leaves one is the process dying mid-send. B2 deliberately abandoned such rows — the reasoning
    was that "we do not know whether the reader saw this" and re-sending risks duplication. The lease
    is what makes recovery safe instead: it is long enough that a row this old cannot be an in-flight
    send, and the ``tag`` derived from ``dedupeKey`` collapses a duplicate at the OS level if the first
    attempt did in fact land.

    ``subscription_id`` is stored as a plain integer rather than a foreign key on purpose: a
    subscription is pruned the moment the push service reports it gone (404/410), and the record that
    we tried to reach it must outlive the address we tried to reach. ``channel`` is the axis a future
    transport (email, mobile push) extends along without a schema change."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("notification_id", "channel", "subscription_id",
                                       name="uq_notification_delivery_target"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    notification_id: Mapped[int] = mapped_column(ForeignKey("notifications.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32), index=True, default="web_push")
    #: The `push_subscriptions.id` at claim time. NOT a foreign key — see the class docstring.
    subscription_id: Mapped[int] = mapped_column(index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    #: pending → success | expired | timeout | transient | permanent. The classification of the LAST
    #: attempt; whether another is coming is `next_attempt_at`, not this.
    status: Mapped[str] = mapped_column(String(16), index=True, default="pending")
    #: The push service's status code where there was one, for correlation with its documentation.
    status_code: Mapped[Optional[int]] = mapped_column(default=None)
    #: A short, non-sensitive reason ("timeout", "http_413"). Never a response body.
    detail: Mapped[str] = mapped_column(String(255), default="")
    attempted_at: Mapped[datetime] = mapped_column(default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(default=None)

    # -- retry scheduling (B3). Persisted rather than held in memory, so the ladder survives a deploy;
    #    a restart in the middle of a fan-out is the ordinary case, not the exotic one.
    #: How many sends have been made. 1 after the first claim — not 0, because the claim IS the attempt.
    attempts: Mapped[int] = mapped_column(default=1)
    #: When the first attempt happened. Never updated, because the age bound that decides whether a
    #: delivery is still worth making must be measured from the beginning, not from the last try.
    first_attempted_at: Mapped[Optional[datetime]] = mapped_column(default=None)
    #: When this row becomes due again. **NULL means nothing is scheduled** — either the delivery is
    #: settled, or it is in flight. This single nullable column is the whole scheduler state; there is
    #: no queue, no timer and nothing in memory to lose.
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(default=None, index=True)


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
    # Location Intelligence Phase 0 (additive): canonical publisher-level location from the
    # Location Resolver (location.py) — ISO 3166-1 alpha-2 / ISO 639-1, NULL when unresolvable.
    # Provider-agnostic by construction: every adapter's metadata arrives here already normalized.
    country: Mapped[Optional[str]] = mapped_column(String(2), default=None)
    language: Mapped[Optional[str]] = mapped_column(String(8), default=None)
    fetched_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ArticleEventLocation(Base):
    """Where an article's EVENT happened (Location Intelligence Phase 2) — 0..n rows per catalog
    article, provider-extracted and normalized by ``location.resolve_event_locations``. The side
    table docs/LOCATION_PLATFORM.md reserved: ``feed_articles`` stays untouched (its ``country``
    remains the publisher's home), and best-known location = event rows when present, publisher
    country otherwise. ``source`` is provider provenance ("gdelt-gkg", "georss", …) — every
    located fact stays auditable; nothing here is ever inferred from article text by us."""

    __tablename__ = "article_event_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_url: Mapped[str] = mapped_column(String(2048), index=True)   # FeedArticle dedup key
    country: Mapped[str] = mapped_column(String(2), index=True)            # ISO 3166-1 alpha-2, upper
    region: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    city: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    lat: Mapped[Optional[float]] = mapped_column(default=None)
    lon: Mapped[Optional[float]] = mapped_column(default=None)
    source: Mapped[str] = mapped_column(String(32), default="provider")


class ArticleEntity(Base):
    """Named entities GDELT's GKG extraction found in a catalog article (X5, rung 2 of
    docs/STORY_ENTITY_EVIDENCE_PLAN.md) — 0..n rows per article, provider-extracted with the
    same contract as :class:`ArticleEventLocation`: never inferred from article text by us,
    provenance on every row, enrichment only. Read by the story builder's X5b entity-merge pass
    when ``RWE_STORY_ENTITY_MERGE > 0`` (adopted 2026-08-16 — one batched query per build);
    populated by the steady-state enricher (``RWE_GDELT_ENTITIES``, deploy default on) and the
    one-shot ``gdelt_entity_backfill`` CLI. Names are stored normalized (lower-cased,
    whitespace-collapsed) so identity comparisons need no re-normalization."""

    __tablename__ = "article_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_url: Mapped[str] = mapped_column(String(2048), index=True)   # FeedArticle dedup key
    kind: Mapped[str] = mapped_column(String(16), index=True)              # "person" | "org"
    name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(32), default="gdelt-gkg")


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
    # Per-feed scheduling state (feed_schedule.py) — NULL for every feed until the scheduler is
    # enabled, and read by nothing while it is off. Kept on this table rather than a new one
    # because it is keyed identically (feed URL) and written on the same cycle by the same code
    # path; a second table would be a join and a second write for one row's worth of state.
    etag: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    last_modified: Mapped[Optional[str]] = mapped_column(String(128), default=None)
    content_sha: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    next_due_at: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    interval_s: Mapped[Optional[float]] = mapped_column(default=None)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


class PublisherMetadata(Base):
    """Cached third-party facts about a publisher — the enrichment side table for the Publisher page.

    **A cache of an external source, never the source of truth.** The curated
    :mod:`outlet_registry` remains authoritative for everything it knows; this table only ever
    fills the gaps it leaves, and every merged field carries its own provenance so the page can say
    where a fact came from. Isolation is identical to :class:`FeedHealth`: no foreign key, no
    influence on corpus construction, clustering, recommendation, or ranking.

    Keyed by a NORMALIZED publisher key (``publisher_key``) rather than the display name, so the
    same outlet reached as "BBC News", "bbc.co.uk" or "BBC  News" hits one row instead of three.

    ``status`` is what makes refresh cheap and idempotent, and it is why a failed lookup is stored
    rather than discarded:

        ok         a page was found, verified, and parsed
        no_match   searched, nothing plausible exists — a NEGATIVE cache entry, so the next cycle
                   does not re-ask Wikipedia the same unanswerable question
        ambiguous  candidates existed but none could be confirmed as this outlet (a disambiguation
                   page, or a website that does not match the domain we actually see them publish
                   from). Recorded as a distinct state from no_match because it is a curation
                   signal: these are the outlets a human should resolve by hand.
        error      the lookup itself failed (network/HTTP). Retried sooner than the others.
    """

    __tablename__ = "publisher_metadata"

    publisher_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    publisher: Mapped[str] = mapped_column(String(255))          # the name the lookup ran for
    status: Mapped[str] = mapped_column(String(16), default="ok", index=True)
    #: WHY the status is what it is — domain / title / domain_conflict / not_an_organisation /
    #: disambiguation / unverified / no_page. Stored because the ambiguous rows are a curation
    #: backlog, and triaging them without this means re-running every lookup to learn what already
    #: happened.
    reason: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    source: Mapped[Optional[str]] = mapped_column(String(16), default=None)   # wikipedia|wikimedia
    # Identity of the matched entity — kept so a match is auditable and re-verifiable by hand.
    wikidata_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)
    wikipedia_title: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    wikipedia_url: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    # The enriched facts. All optional: a partial match is a normal, useful outcome.
    description: Mapped[Optional[str]] = mapped_column(Text, default=None)
    founded: Mapped[Optional[str]] = mapped_column(String(32), default=None)   # year, or ISO date
    headquarters: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    country: Mapped[Optional[str]] = mapped_column(String(2), default=None)    # ISO 3166-1 alpha-2
    website: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    parent: Mapped[Optional[str]] = mapped_column(String(255), default=None)
    logo: Mapped[Optional[str]] = mapped_column(String(1024), default=None)
    # Every OTHER field's provenance is fixed by which extractor produces it (description comes
    # from the article, the rest from Wikidata claims). The logo is the one fact reachable two
    # ways — a Commons file named by claim P154, or the article's own page image — so it carries
    # its provenance explicitly rather than having it guessed back out of the URL host.
    logo_source: Mapped[Optional[str]] = mapped_column(String(16), default=None)
    error: Mapped[Optional[str]] = mapped_column(Text, default=None)
    fetched_at: Mapped[datetime] = mapped_column(default=_utcnow, index=True)


class StoryMember(Base):
    """article url -> the story id it was last served under. The memory that makes a story id stable.

    ``_story_id`` derives the id from the cluster's earliest-published member, which is stable for
    the case it was designed against — a LATER article joining never disturbs it — and unstable for
    two that happen constantly. The candidate set is a rolling time window, so every cluster
    eventually loses its oldest article; and ingestion is not ordered by publication time, so
    GDELT's backfill attaches articles published earlier than the current anchor. **Measured on the
    live catalog: 5.1% of surviving stories changed id per day, 72 of 81 cases from the
    representative ageing out.** A story id is what a saved or shared link points at.

    No member-derived anchor can fix that, because the failure is the anchor LEAVING. The fix is
    memory: remember which id a cluster's articles were last served under, and give the id back to
    whichever cluster still holds most of them.

    One row per article url rather than a list per story, because the lookup this table exists for
    is "which story did this url belong to" — an inverted index is the natural shape, and it keeps
    the reassignment a counting problem instead of a set-comparison problem.

    Rewritten wholesale on each build from the CURRENT window, which prunes it for free: a url that
    has aged out is not being served, so its history is not worth keeping. No foreign key and no
    influence on clustering — this only renames the output.
    """

    __tablename__ = "story_member"

    url: Mapped[str] = mapped_column(String(1024), primary_key=True)
    story_id: Mapped[str] = mapped_column(String(32), index=True)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow)


class AnalyticsEvent(Base):
    """A single product-analytics event (PA1) — the raw material for the activation funnel + metrics.

    **Measurement only.** No recommender, report, ranking, lifecycle, or personalization path reads
    this table; it is a pseudonymous record of a UI moment (see ``product_analytics.EVENTS``), written
    by the ``/api/events`` sink and read back only by the internal analytics dashboard. ``user_id`` is
    resolved server-side from the trusted web tier (never client-asserted); ``anon_id`` carries the
    anonymous (pre-account) identity so the funnel spans the sign-in boundary. Properties are a small,
    allow-listed, truncated scalar set (``product_analytics.PROPS``) — no PII, no free-form blob. The
    ``user_id`` FK is intentionally omitted so an event is never coupled to a user's lifecycle (and an
    anonymous event has none); isolation identical to :class:`FeedHealth`."""

    __tablename__ = "analytics_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(index=True, default=None)  # resolved server-side; nullable (anon)
    anon_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, default=None)
    session_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    props: Mapped[Optional[str]] = mapped_column(Text, default=None)          # JSON of the allow-listed props
    client_ts: Mapped[Optional[str]] = mapped_column(String(64), default=None)  # client ISO (advisory)
    server_ts: Mapped[str] = mapped_column(String(64), index=True)            # authoritative receive time (ISO)
    request_id: Mapped[Optional[str]] = mapped_column(String(32), default=None)  # OBS1 correlation id
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
        self._ensure_media_columns()
        self._ensure_source_columns()
        self._ensure_location_columns()
        self._ensure_read_columns()
        self._ensure_lifecycle_columns()
        self._ensure_publisher_metadata_columns()
        self._ensure_delivery_retry_columns()
        self._ensure_feed_schedule_columns()
        self._ensure_search_indexes()
        self._ensure_retention_indexes()

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
                                display_name: str | None = None,
                                *, refresh_profile: bool = True) -> User:
        """Return the user for ``(provider, provider_account_id)``, creating the user +
        identity on first sign-in.

        Idempotent: the same identity always resolves to the same user, whether calls are
        sequential or concurrent. A returning user's email / display name are refreshed when a
        value is supplied (Google can change them), and left as-is when ``None``.

        ``refresh_profile=False`` suppresses that refresh for an EXISTING user, so the supplied
        profile can never overwrite what is stored. Creation is unaffected — a first sighting is
        still created with the email and display name it was given, because creation is not a
        refresh. The caller that needs this is web-tier identity recovery, which resolves an id
        from a session token that may be weeks old: without it, a long-idle broken session could
        write a stale profile over one a newer sign-in had already updated
        (``docs/SESSION_IDENTITY_RECOVERY_DESIGN.md`` §10, S2).

        Two concurrent first sign-ins for the same identity both miss the initial read; the
        UNIQUE constraint on ``(provider, provider_account_id)`` decides which one creates the
        rows, and the loser resolves the winner's user in a **second transaction** rather than
        failing. Its first transaction rolled back whole, so nothing of it was committed and the
        retry is safe — that is the property the whole design rests on, and it is why there is
        no SAVEPOINT here (under the sqlite3 driver's legacy transaction mode a released
        savepoint escapes the enclosing rollback, which would commit a user for a call that
        raised).

        Full contract, proof and the assumptions it depends on:
        ``docs/IDENTITY_UPSERT_CONCURRENCY.md``; the harness that checks them is
        ``tests/concurrency/``.
        """
        try:
            return self._resolve_identity(provider, provider_account_id, email, display_name,
                                          create=True, refresh_profile=refresh_profile)
        except (IntegrityError, OperationalError) as first:
            # Either we lost a first-sighting race (the UNIQUE constraint rejected our identity
            # insert) or we could not get the write transaction against a concurrent writer.
            # OperationalError is caught deliberately: if the driver ever stops running in legacy
            # transaction mode, a lost race can surface as a snapshot conflict instead.
            user = self._resolve_identity(provider, provider_account_id, email, display_name,
                                          create=False, refresh_profile=refresh_profile)
            if user is None:
                raise first     # nobody won, so this was never a race — never swallow it
            return user

    def _resolve_identity(self, provider: str, provider_account_id: str,
                          email: str | None, display_name: str | None,
                          *, create: bool, refresh_profile: bool = True) -> "User | None":
        """One attempt at :meth:`upsert_user_by_identity`, in one transaction.

        With ``create=False`` it resolves an existing identity or returns ``None`` — which the
        caller reads as "there was no winner, so the failure was not a race".

        ``refresh_profile=False`` skips the refresh of an EXISTING user's profile. Creation is
        unaffected: the profile is written by the ``User(...)`` constructor below, not by the
        refresh block, so a first sighting still gets its email and display name either way."""
        with self.session() as s:
            identity = s.scalar(
                select(Identity).where(Identity.provider == provider,
                                       Identity.provider_account_id == provider_account_id))
            if identity is None:
                if not create:
                    return None
                user = User(email=email, display_name=display_name)
                s.add(user)
                s.flush()                       # assign user.id
                s.add(Identity(provider=provider,
                               provider_account_id=provider_account_id, user_id=user.id))
                s.flush()                       # surface the conflict here, not at commit
            else:
                user = identity.user
            # The refresh, and the only thing refresh_profile turns off. On the create path above the
            # profile is already on the new row, so this block is a no-op there; here it is what lets a
            # returning reader's changed Google profile land. Skipping it makes the whole transaction
            # read-only for an existing identity — nothing is dirty, so `flush` emits no UPDATE.
            if refresh_profile:
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

    def report_eval_snapshots(self, user_id: int, limit: int = 120) -> list:
        """Per-snapshot inputs for recommendation evaluation (RC2.5), **oldest-first**: each saved
        report's full-ISO ``date`` (createdAt), real ``reads`` (from ``coverage``), ``mode``, per-metric
        ``{key: score}``, and the per-metric estimated-impact band ``{metric: {low, high}}`` taken from
        the stored improvements' ``impactEstimate``. Read-only — it parses the stored JSON and recomputes
        **nothing** (the same discipline as :meth:`report_metric_series`)."""
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
            estimates = {}
            for imp in (rep.get("improvements") or []):
                est, mk = imp.get("impactEstimate"), imp.get("metric")
                if est and mk is not None and est.get("low") is not None and est.get("high") is not None:
                    estimates[mk] = {"low": int(est["low"]), "high": int(est["high"])}
            cov = rep.get("coverage") or {}
            out.append({"date": r.created_at.isoformat() if r.created_at else None,
                        "reads": int(cov["reads"]) if cov.get("reads") is not None else None,
                        "mode": rep.get("mode"), "metrics": metrics, "estimates": estimates})
        return out

    def list_users_with_improvement_lifecycle(self) -> list:
        """Distinct user ids that have any improvement-lifecycle row — the cohort the offline rule-quality
        evaluation (RC2.5) iterates. Read-only."""
        with self.session() as s:
            rows = s.scalars(select(ImprovementLifecycle.user_id).distinct()).all()
        return [int(u) for u in rows]

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
                            source_provider: "str | None" = None, external_id: "str | None" = None,
                            country: "str | None" = None, language: "str | None" = None) -> bool:
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
                    country=country, language=language,
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
            # Location backfill-when-empty: the same-canonical-URL merge fills location the first
            # time any source supplies it, and never rewrites a stored value (first-seen wins,
            # like every other first-seen field on this row).
            if country and not row.country:
                row.country = country
            if language and not row.language:
                row.language = language
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

    # ---- storage lifecycle: incremental prunes (see examples/storage_lifecycle.py) ----------
    # Every prune here is BOUNDED by `limit` so a cleanup pass holds the single SQLite write lock
    # only briefly and can never stall ingestion; the orchestrator re-runs until a pass is a no-op.
    # None of these touch a table in retention_policy.PROTECTED_TABLES.

    def article_event_countries(self, canonical_url: str) -> list:
        """One article's provider-extracted EVENT countries (ISO-3166-1 alpha-2, sorted).

        Read-only companion to :meth:`replace_article_event_locations`. Coverage Comparison needs
        the article's own located facts to compare against the story's consensus; it must never
        infer a location from text, so an article with no rows returns ``[]`` and the comparison
        reports "not comparable" rather than an omission."""
        with self._Session() as s:
            rows = s.execute(select(ArticleEventLocation.country)
                             .where(ArticleEventLocation.canonical_url == canonical_url)).all()
        return sorted({str(r[0]).upper() for r in rows if r[0]})

    def prune_orphan_event_locations(self, limit: int = 5000) -> int:
        """Delete ``article_event_locations`` rows whose article is gone from the catalog.

        Catalog retention deletes ``feed_articles`` only (that method's contract), and event
        locations are a side table keyed by canonical URL with no foreign key — so every pruned
        article used to leave its geography rows behind forever. This is the reaper for them.
        Harmless when retention is off: with no pruned articles there are no orphans."""
        with self.session() as s:
            ids = [i for (i,) in s.execute(
                select(ArticleEventLocation.id)
                .where(~select(FeedArticle.canonical_url)
                       .where(FeedArticle.canonical_url == ArticleEventLocation.canonical_url)
                       .exists())
                .limit(limit)).all()]
            if not ids:
                return 0
            res = s.execute(delete(ArticleEventLocation).where(ArticleEventLocation.id.in_(ids)))
            s.commit()
            return res.rowcount or 0

    def prune_scored_cache(self, max_age_days: int, limit: int = 5000) -> int:
        """Drop score-cache entries older than ``max_age_days``. Pure cache: ``score_with_cache``
        re-derives an entry deterministically on the next read, so this costs a little CPU and
        never loses information. 0 = keep forever."""
        if max_age_days <= 0:
            return 0
        cutoff = _utcnow() - timedelta(days=max_age_days)
        with self.session() as s:
            urls = [u for (u,) in s.execute(
                select(ScoredArticle.url).where(ScoredArticle.created_at < cutoff)
                .limit(limit)).all()]
            if not urls:
                return 0
            res = s.execute(delete(ScoredArticle).where(ScoredArticle.url.in_(urls)))
            s.commit()
            return res.rowcount or 0

    def prune_analytics_events(self, max_age_days: int, limit: int = 5000) -> int:
        """Drop product-analytics events older than ``max_age_days``. Observational telemetry for
        the activation funnel — a rolling window is the honest retention for it. 0 = keep forever."""
        if max_age_days <= 0:
            return 0
        cutoff = _utcnow() - timedelta(days=max_age_days)
        with self.session() as s:
            ids = [i for (i,) in s.execute(
                select(AnalyticsEvent.id).where(AnalyticsEvent.created_at < cutoff)
                .limit(limit)).all()]
            if not ids:
                return 0
            res = s.execute(delete(AnalyticsEvent).where(AnalyticsEvent.id.in_(ids)))
            s.commit()
            return res.rowcount or 0

    def prune_rec_events(self, max_age_days: int, limit: int = 5000) -> int:
        """Drop recommendation surface/open events older than ``max_age_days``.

        These are the denominator+numerator of Open-Mindedness, so the default window is a full
        year: long enough that no live metric loses input, bounded enough that the table cannot
        grow forever. 0 = keep forever."""
        if max_age_days <= 0:
            return 0
        cutoff = (_utcnow() - timedelta(days=max_age_days)).isoformat()
        with self.session() as s:
            ids = [i for (i,) in s.execute(
                select(RecEvent.id).where(RecEvent.shown_at < cutoff).limit(limit)).all()]
            if not ids:
                return 0
            res = s.execute(delete(RecEvent).where(RecEvent.id.in_(ids)))
            s.commit()
            return res.rowcount or 0

    def prune_report_snapshots(self, keep_per_user: int, limit: int = 5000) -> int:
        """Keep only the newest ``keep_per_user`` report snapshots per user (the analytics trend
        series). Old snapshots beyond the cap add no chart the reader can see. 0 = keep forever."""
        if keep_per_user <= 0:
            return 0
        removed = 0
        with self.session() as s:
            uids = [u for (u,) in s.execute(select(ReportSnapshot.user_id).distinct()).all()]
            for uid in uids:
                keep_ids = [i for (i,) in s.execute(
                    select(ReportSnapshot.id).where(ReportSnapshot.user_id == uid)
                    .order_by(ReportSnapshot.id.desc()).limit(keep_per_user)).all()]
                if len(keep_ids) < keep_per_user:
                    continue                       # under the cap — nothing to do for this user
                stale = [i for (i,) in s.execute(
                    select(ReportSnapshot.id)
                    .where(ReportSnapshot.user_id == uid, ReportSnapshot.id.notin_(keep_ids))
                    .limit(max(1, limit - removed))).all()]
                if not stale:
                    continue
                res = s.execute(delete(ReportSnapshot).where(ReportSnapshot.id.in_(stale)))
                removed += res.rowcount or 0
                if removed >= limit:
                    break
            s.commit()
        return removed

    def storage_stats(self) -> dict:
        """Row counts per table + the database file size — the input to the size alerts and the
        ops probe. Read-only and cheap (COUNT over indexed tables)."""
        import pathlib
        counts = {}
        with self.session() as s:
            for name, model in (("feed_articles", FeedArticle), ("article_event_locations", ArticleEventLocation),
                                ("scored_articles", ScoredArticle), ("analytics_events", AnalyticsEvent),
                                ("rec_events", RecEvent), ("report_snapshots", ReportSnapshot),
                                ("notifications", Notification), ("reads", Read),
                                ("push_subscriptions", PushSubscription),
                                ("saved_articles", SavedArticle), ("users", User)):
                counts[name] = int(s.scalar(select(func.count()).select_from(model)) or 0)
        size = None
        url = str(self.engine.url)
        if url.startswith("sqlite:///") and not url.endswith(":memory:"):
            p = pathlib.Path(url.replace("sqlite:///", "", 1))
            if p.exists():
                size = p.stat().st_size + sum(
                    q.stat().st_size for q in (p.with_suffix(p.suffix + "-wal"),
                                               p.with_suffix(p.suffix + "-shm")) if q.exists())
        return {"rows": counts, "dbBytes": size}

    def count_feed_articles(self) -> int:
        """How many distinct catalog articles have been ingested."""
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(FeedArticle)) or 0)

    def catalog_fingerprint(self) -> tuple:
        """A cheap change token for the catalog: ``(row count, newest fetched_at)``.

        Used to invalidate derived caches (story clustering). A bare COUNT is **not** sufficient:
        deleting N rows and inserting N others leaves it identical while the content is entirely
        different — which is exactly what a retention prune plus an ingest in the same interval
        does. ``fetched_at`` is indexed and monotonic per write, so the pair moves on any insert,
        delete or re-poll."""
        with self.session() as s:
            n = int(s.scalar(select(func.count()).select_from(FeedArticle)) or 0)
            newest = s.scalar(select(func.max(FeedArticle.fetched_at)))
        return (n, newest.isoformat() if newest is not None else None)

    def list_feed_articles(self, limit: int = 50) -> list:
        """Catalog articles, most-recently-fetched first (capped at ``limit``)."""
        with self.session() as s:
            rows = s.scalars(select(FeedArticle)
                             .order_by(FeedArticle.fetched_at.desc())
                             .limit(limit)).all()
            return [self._feed_row(r) for r in rows]

    def list_retention_rows(self) -> list:
        """The whole catalogue as the NARROW projection retention actually reads (M3 / D1).

        ``list_feed_articles`` returns every column and JSON-parses the full ``scored`` payload per
        row. Retention reads **six fields** of that, and paying for the rest is what made the pass
        expensive. Measured at 150,000 rows — production's shape on 2026-08-27:

            list_feed_articles(limit=10M)   7.77 s   +888.9 MB RSS   (6.07 KB/row)
            this projection                 0.54 s   + 46.9 MB RSS   (0.32 KB/row)

        **14× faster and 19× smaller, for byte-identical decisions** — `plan_retention` and
        `corpus_metrics` see exactly the fields they read, so the prune set cannot differ. On a
        4 GiB box that moves the point where a retention pass exhausts memory from ~675,000 rows to
        ~12.8 million.

        The projection is exactly what `corpus_health` consults, and no more:

        * ``canonicalUrl`` / ``url`` — ``_canonical``. Both, though ``canonical_url`` is the primary
          key and cannot be NULL: a deletion path is not where to save 12 MB by arguing that a
          fallback is unreachable.
        * ``publisher`` + ``scored.outlet`` — ``_outlet``
        * ``scored.lean`` — ``_bucket``, via the ``ix_feed_lean`` expression index's own expression
        * ``publishedAt`` / ``fetchedAt`` — ``_published``
        * ``title`` — ``_missing_metadata``, which is a ``corpus_metrics`` field

        Deliberately NOT included: ``createdAt``. It is read only through
        ``_CANDIDACY_TIME_KEYS``, which is candidate freshness, not retention — so a caller handing
        these rows to the candidacy path would get subtly different ages. **These rows are for
        retention and its metrics; they are not FeedArticle rows.**
        """
        sql = text(
            "SELECT canonical_url, url, publisher, title, published_at, fetched_at, "
            "       json_extract(scored, '$.outlet') AS outlet, "
            "       json_extract(scored, '$.lean')   AS lean "
            "FROM feed_articles")
        with self.session() as s:
            return [{"canonicalUrl": r[0], "url": r[1], "publisher": r[2] or "",
                     "title": r[3] or "", "publishedAt": r[4],
                     # `fetched_at` is a DATETIME the ORM would hand back as a datetime; the driver
                     # gives the raw string here, and `_published` parses ISO text either way.
                     "fetchedAt": r[5] if isinstance(r[5], str) else (r[5].isoformat() if r[5] else None),
                     "scored": {"outlet": r[6], "lean": r[7]}}
                    for r in s.execute(sql)]

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
                "country": r.country, "language": r.language,
                "fetchedAt": r.fetched_at.isoformat() if r.fetched_at else None,
                "createdAt": r.created_at.isoformat() if r.created_at else None}

    def replace_article_event_locations(self, canonical_url: str, locations) -> None:
        """Persist an article's EVENT locations (``location.EventLocation`` rows) — idempotent per
        provider: incoming rows replace this article's rows FROM THE SAME SOURCES only, so a
        provider that supplies no geography never wipes another provider's facts (the same
        first-seen/backfill discipline the dedup merge uses)."""
        locs = [l for l in (locations or ()) if getattr(l, "country", None)]
        if not canonical_url or not locs:
            return
        sources = sorted({l.source for l in locs})
        with self.session() as s:
            s.execute(delete(ArticleEventLocation).where(
                ArticleEventLocation.canonical_url == canonical_url,
                ArticleEventLocation.source.in_(sources)))
            for l in locs:
                s.add(ArticleEventLocation(canonical_url=canonical_url, country=l.country,
                                           region=l.region, city=l.city, lat=l.lat, lon=l.lon,
                                           source=l.source))

    def replace_article_entities(self, canonical_url: str, entities: dict, *,
                                 source: str = "gdelt-gkg") -> int:
        """Persist an article's named entities — ``{"person": [names], "org": [names]}`` —
        idempotent PER SOURCE like :meth:`replace_article_event_locations`: incoming rows replace
        this article's rows from the same source only. Names are stored as given (the enricher
        normalizes); empty input is a no-op rather than a delete, so a GKG record that carries no
        entities never erases an earlier one that did. Returns rows written."""
        rows = [(kind, name) for kind in ("person", "org")
                for name in (entities or {}).get(kind, ()) if name]
        if not canonical_url or not rows:
            return 0
        with self.session() as s:
            s.execute(delete(ArticleEntity).where(
                ArticleEntity.canonical_url == canonical_url,
                ArticleEntity.source == source))
            for kind, name in rows:
                s.add(ArticleEntity(canonical_url=canonical_url, kind=kind,
                                    name=name[:255], source=source))
        return len(rows)

    def entities_for_urls(self, canonical_urls) -> dict:
        """``{canonical_url: {"person": sorted names, "org": sorted names}}`` — the batched
        lookup the X5 instruments use, shaped like :meth:`event_countries_for_urls`. URLs
        without entity rows are absent."""
        urls = [u for u in dict.fromkeys(canonical_urls) if u]
        if not urls:
            return {}
        out: dict = {}
        with self.session() as s:
            for i in range(0, len(urls), 500):
                rows = s.execute(select(ArticleEntity.canonical_url, ArticleEntity.kind,
                                        ArticleEntity.name).distinct()
                                 .where(ArticleEntity.canonical_url.in_(urls[i:i + 500]))).all()
                for u, kind, name in rows:
                    out.setdefault(u, {}).setdefault(kind, []).append(name)
        return {u: {k: sorted(v) for k, v in kinds.items()} for u, kinds in out.items()}

    def count_article_entities(self) -> int:
        """Total entity rows — the backfill's before/after fact and the coverage probe's input."""
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(ArticleEntity)) or 0)

    def backfill_article_image(self, canonical_url: str, image_url: str, *,
                              source: str = "gdelt-gkg") -> bool:
        """Set an article's image ONLY when it has none (the backfill-when-empty discipline —
        a feed-provided image is never overwritten). The GKG enricher's thumbnail supply for
        articles whose feed shipped no media tags. True when a row was actually updated."""
        url = (image_url or "").strip()
        if not canonical_url or not url.lower().startswith(("http://", "https://")):
            return False
        with self.session() as s:
            row = s.get(FeedArticle, canonical_url)
            if row is None or (row.image or "").strip():
                return False
            row.image = url
            row.image_source = source
            return True

    def existing_feed_urls(self, canonical_urls) -> set:
        """Which of these canonical URLs are catalog articles — the batched membership check the
        GKG enricher uses so enrichment only ever touches articles we actually hold."""
        urls = [u for u in dict.fromkeys(canonical_urls) if u]
        out: set = set()
        with self.session() as s:
            for i in range(0, len(urls), 500):
                rows = s.scalars(select(FeedArticle.canonical_url)
                                 .where(FeedArticle.canonical_url.in_(urls[i:i + 500]))).all()
                out.update(rows)
        return out

    def count_event_locations(self) -> int:
        """Total event-location rows — the GKG enricher's cold-start probe (0 + a non-empty
        catalog ⇒ its first cycle auto-backfills a deep window range)."""
        with self.session() as s:
            return int(s.execute(select(func.count()).select_from(ArticleEventLocation)).scalar_one())

    def event_countries_for_urls(self, canonical_urls) -> dict:
        """Distinct EVENT countries per catalog article, keyed by canonical URL — the batched
        lookup the Story Service uses to locate members. URLs without event rows are absent."""
        urls = [u for u in dict.fromkeys(canonical_urls) if u]
        if not urls:
            return {}
        out: dict = {}
        with self.session() as s:
            for i in range(0, len(urls), 500):
                rows = s.execute(select(ArticleEventLocation.canonical_url,
                                        ArticleEventLocation.country).distinct()
                                 .where(ArticleEventLocation.canonical_url.in_(urls[i:i + 500]))).all()
                for u, c in rows:
                    out.setdefault(u, []).append(c)
        return {u: sorted(cs) for u, cs in out.items()}

    def feed_article_country_facets(self, include_provisional: bool = True,
                                    include_shadow: bool = False) -> list:
        """Per-country catalog facts (EVENT dimension since Phase 2): article count + distinct
        publishers per country, most-covered first. An article counts toward the countries its
        EVENTS happened in (``article_event_locations``) — never toward its publisher's home,
        which is a separate provenance fact. Before event geography flows (the GKG enricher),
        this is honestly empty, so country pickers offer nothing rather than the wrong thing.
        ``include_provisional=False`` (the Discover surface) keeps the counts consistent with
        what that surface actually lists — same convention as :meth:`feed_article_facets`."""
        located = (select(ArticleEventLocation.canonical_url.label("u"),
                          ArticleEventLocation.country.label("c")).distinct().subquery())
        with self.session() as s:
            stmt = (select(located.c.c, func.count(func.distinct(located.c.u)),
                           func.count(func.distinct(FeedArticle.publisher)))
                    .select_from(located.join(FeedArticle,
                                              FeedArticle.canonical_url == located.c.u))
                    .group_by(located.c.c))
            if not include_provisional:
                stmt = stmt.where(or_(FeedArticle.article_state.is_(None),
                                      FeedArticle.article_state != "provisional"))
            if not include_shadow:
                import corpus
                shadow = corpus.shadow_exclusions()
                if shadow:
                    stmt = stmt.where(or_(FeedArticle.publisher.is_(None),
                                          func.lower(FeedArticle.publisher).notin_(sorted(shadow))))
            rows = s.execute(stmt).all()
        out = [{"country": c, "articles": int(n), "publishers": int(p)} for c, n, p in rows]
        out.sort(key=lambda r: (-r["articles"], r["country"]))
        return out

    def feed_article_locations(self, canonical_urls) -> dict:
        """Location + publisher for a set of catalog articles, keyed by canonical URL — the batched
        join Geographic Diversity readiness uses to locate a reader's stored reads. Same batching
        discipline as :meth:`feed_article_media`; rows the catalog doesn't know are simply absent."""
        urls = [u for u in dict.fromkeys(canonical_urls) if u]
        if not urls:
            return {}
        out: dict = {}
        with self.session() as s:
            for i in range(0, len(urls), 500):
                rows = s.scalars(select(FeedArticle)
                                 .where(FeedArticle.canonical_url.in_(urls[i:i + 500]))).all()
                for r in rows:
                    out[r.canonical_url] = {"country": r.country, "language": r.language,
                                            "publisher": r.publisher}
        return out

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
                    # The article's SCORED (registry-scale) lean, under its own key so consumers
                    # opt in explicitly: the rec enrichment rewrites the card's lean with it (one
                    # UI value space — docs/LEAN_CONSISTENCY.md F1), while the history attach
                    # copies only publishedAt and never sees it.
                    try:
                        lv = (json.loads(r.scored) or {}).get("lean")
                        rec["catalogLean"] = float(lv) if lv is not None else None
                    except (TypeError, ValueError):
                        rec["catalogLean"] = None
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

    def _ensure_feed_schedule_columns(self) -> None:
        """Additive, idempotent per-feed scheduling columns on ``feed_health`` — same discipline as
        ``_ensure_media_columns``, and here for the same reason ``_ensure_publisher_metadata_columns``
        exists: ``create_all`` creates NEW tables only, and this table shipped long ago."""
        for name, decl in [("etag", "VARCHAR(512)"), ("last_modified", "VARCHAR(128)"),
                           ("content_sha", "VARCHAR(64)"), ("next_due_at", "VARCHAR(64)"),
                           ("interval_s", "FLOAT")]:
            try:
                with self.session() as s:
                    s.execute(text(f"ALTER TABLE feed_health ADD COLUMN {name} {decl}"))
            except Exception:
                pass    # already exists (fresh DB) or a non-sqlite backend — nothing to do

    def _ensure_publisher_metadata_columns(self) -> None:
        """Additive, idempotent columns on ``publisher_metadata`` — same discipline as
        ``_ensure_media_columns``, and here for the same reason it exists there.

        ``create_all`` creates NEW tables only. This table shipped one deploy, gained ``reason`` the
        next, and the live DB kept the original schema: every read then failed with
        ``no such column: publisher_metadata.reason``. Any column added to
        :class:`PublisherMetadata` after its first deploy belongs in this list."""
        for name, decl in [("reason", "VARCHAR(32)"), ("logo_source", "VARCHAR(16)")]:
            try:
                with self.session() as s:
                    s.execute(text(f"ALTER TABLE publisher_metadata ADD COLUMN {name} {decl}"))
            except Exception:
                pass    # already exists (fresh DB) or a non-sqlite backend — nothing to do

    def _ensure_location_columns(self) -> None:
        """Additive, idempotent location columns on ``feed_articles`` (Location Intelligence
        Phase 0), upgrading pre-existing DBs in place exactly like ``_ensure_source_columns``.
        Legacy rows keep ``NULL`` until a poll refreshes them (backfill-when-empty on merge)."""
        for name, decl in [("country", "VARCHAR(2)"), ("language", "VARCHAR(8)")]:
            try:
                with self.session() as s:
                    s.execute(text(f"ALTER TABLE feed_articles ADD COLUMN {name} {decl}"))
            except Exception:
                pass    # already exists (fresh DB) or a non-sqlite backend — nothing to do
        try:
            with self.session() as s:
                s.execute(text("CREATE INDEX IF NOT EXISTS ix_feed_country ON feed_articles(country)"))
        except Exception:
            pass

    def _ensure_delivery_retry_columns(self) -> None:
        """Additive, idempotent retry-scheduler columns on ``notification_deliveries`` (Phase B3).

        Same discipline and the same reason as ``_ensure_publisher_metadata_columns``: ``create_all``
        creates NEW tables only, and this one shipped in B2. Without this, the first B3 deploy would
        fail every ledger read with ``no such column: notification_deliveries.attempts`` — and since
        the ledger is read on the delivery path, push would stop working entirely on a database that
        already had rows in it.

        ``attempts`` defaults to 1 for legacy rows, which is true of every one of them: a B2 row was
        claimed exactly once. ``next_attempt_at`` stays NULL, so nothing that predates B3 is suddenly
        scheduled — those deliveries were settled under the old rules and stay settled.

        ``first_attempted_at`` is **backfilled from ``attempted_at``**, and that is not cosmetic. The
        age bound — the rule that stops a four-hour-old "breaking news" being delivered as if it were
        current — is measured from it, and an unknown start time is deliberately read as "not expired"
        so a delivery is never abandoned for a fact not in evidence. Left NULL, every B2 row that the
        process had claimed and never resolved would come due on the first B3 deploy with the age
        bound inert, and be sent however old it was. On a row that was never resolved, ``attempted_at``
        IS the first attempt, so the backfill is exact rather than a guess."""
        for name, decl in [("attempts", "INTEGER DEFAULT 1"),
                           ("first_attempted_at", "DATETIME"),
                           ("next_attempt_at", "DATETIME")]:
            try:
                with self.session() as s:
                    s.execute(text(f"ALTER TABLE notification_deliveries ADD COLUMN {name} {decl}"))
            except Exception:
                pass    # already exists (fresh DB) or a non-sqlite backend — nothing to do
        try:
            with self.session() as s:
                s.execute(text("UPDATE notification_deliveries SET first_attempted_at = attempted_at "
                               "WHERE first_attempted_at IS NULL"))
        except Exception:
            pass        # nothing to backfill, or a backend that rejected the ALTER above
        try:
            with self.session() as s:
                # The scheduler's only hot query filters on this. Small table today; the index is
                # cheap now and impossible to add unnoticed once it is not.
                s.execute(text("CREATE INDEX IF NOT EXISTS ix_delivery_next_attempt "
                               "ON notification_deliveries(next_attempt_at)"))
        except Exception:
            pass

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
                      "CREATE INDEX IF NOT EXISTS ix_feed_category ON feed_articles(json_extract(scored,'$.category'))",
                      # Publisher filtering is CASE-INSENSITIVE (`lower(publisher) = ?`), and a
                      # function applied to a column makes the plain ix_feed_publisher unusable —
                      # measured in production: `SCAN feed_articles`, a full table scan for every
                      # publisher filter, every Publisher page and every publisher-scoped search.
                      # An expression index over the same expression restores the lookup.
                      # Measured at 25,000 rows: 28.7 ms -> 1.8 ms, and the plan flips from SCAN to
                      # SEARCH. It matters more later than now: RWE_RETENTION_MAX_COUNT allows
                      # 150,000 rows, six times the current catalog, and a scan grows with all of it.
                      "CREATE INDEX IF NOT EXISTS ix_feed_publisher_lower ON feed_articles(lower(publisher))"]
        # See `_create_indexes` for the one-transaction-per-statement rule and why it exists.
        self.index_errors: "list[tuple[str, str]]" = []
        self._create_indexes(stmts)

    def _ensure_retention_indexes(self) -> None:
        """Additive, idempotent indexes on the columns the bounded prunes FILTER by (M3 / D3).

        Three of the five prunes in ``storage_lifecycle.run_cleanup`` had no index on the column
        their ``WHERE`` tests, so each one full-scanned its table on every pass — including the
        overwhelming majority of passes that delete nothing, because proving there is nothing to
        delete is exactly what the scan is for. ``EXPLAIN QUERY PLAN`` reported ``SCAN`` for all
        three, and measured at 400,000 rows with catalogue retention off:

            scored_articles   117.6 ms   of a 235.8 ms pass   <- the one that matters
            analytics_events    1.9 ms   (an empty table here; a scan of nothing)
            rec_events          1.5 ms   (likewise)

        ``scored_articles`` is the one to care about: it holds a row per article for
        ``RWE_RETENTION_SCORED_DAYS`` (default 30), so at the 50,000-source target it is a 7.5 M-row
        table scanned on every cleanup pass — extrapolating to ~2.2 s of held ingest lock, to find
        rows an index answers in microseconds. ``analytics_events`` already has four indexes and
        none is on ``created_at`` (``ix_analytics_events_server_ts`` is a different column);
        ``rec_events`` is indexed on ``user_id`` only.

        Separate from :meth:`_ensure_search_indexes` because these are not search indexes and
        naming them so would be the wrong signpost for whoever reads this next — but it shares
        :meth:`_create_indexes`, so it inherits the one-transaction-per-statement rule and the
        recorded failures. Purely additive: an index changes speed, never results.
        """
        self._create_indexes([
            "CREATE INDEX IF NOT EXISTS ix_scored_created_at ON scored_articles(created_at)",
            "CREATE INDEX IF NOT EXISTS ix_analytics_created_at ON analytics_events(created_at)",
            "CREATE INDEX IF NOT EXISTS ix_rec_events_shown_at ON rec_events(shown_at)",
        ])

    def _create_indexes(self, stmts) -> None:
        """Run ``CREATE INDEX IF NOT EXISTS`` statements, appending any failure to
        :attr:`index_errors`.

        ONE TRANSACTION PER STATEMENT, and a failure is RECORDED rather than swallowed.

        The old shape wrapped the whole loop in a single session and a single bare ``except: pass``,
        which has two faults that only showed up when an index was added. A failure on any one
        statement aborted every statement after it AND rolled back the ones before it in the same
        transaction — so adding a new index at the end of the list could leave a fresh database with
        NO indexes at all. And the failure was invisible: production reported ``SCAN feed_articles``
        with an index that was supposed to exist, and nothing anywhere said why. "Never blocks
        startup" was the right intent; "never tells anyone" was not part of it.

        ``index_errors`` is diagnostic state, not control flow — nothing reads it to decide
        anything, so a database that refuses every index still serves, exactly as before. It is
        surfaced by :meth:`storage_diagnostics` as ``indexErrors``, which is what makes it a
        diagnostic rather than a variable: it was written and read by nobody until M3 added a second
        writer to it.
        """
        if not hasattr(self, "index_errors"):
            self.index_errors = []
        for stmt in stmts:
            try:
                with self.session() as s:
                    s.execute(text(stmt))
            except Exception as exc:                       # pragma: no cover - environment-specific
                name = stmt.split()[5] if len(stmt.split()) > 5 else stmt[:40]
                self.index_errors.append((name, f"{type(exc).__name__}: {exc}"))

    @staticmethod
    def _lean_expr():
        return func.json_extract(FeedArticle.scored, "$.lean")

    @staticmethod
    def _category_expr():
        return func.json_extract(FeedArticle.scored, "$.category")

    def _search_conditions(self, *, q, publisher, lean, topic, date_from, date_to, source,
                           country=None) -> list:
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
        if country and str(country).strip():
            # EVENT location only (Phase 2): ?country= means "articles about events in that
            # country". The publisher's home (FeedArticle.country) is a separate PROVENANCE
            # dimension — reader analytics and publisher intelligence read it; content filters
            # never do. Same rule as stories + facets, so every surface agrees.
            #
            # UNCORRELATED, and that is the whole point. This was a correlated EXISTS keyed on the
            # outer row's canonical_url, so the companion count() re-evaluated it once per catalog
            # row — a full scan with a per-row subquery whose cost tracked the CATALOG rather than
            # the located set. Measured in production (2026-08-02): 5,366 ms for ?country=IL
            # against a web tier that abandons every engine call at 6,000 ms, so the home rail's
            # "From your places" card 503'd and blanked whenever anything else was running, for
            # every country equally (the scan does not care how many articles the country has).
            # Selecting the located urls ONCE and testing membership is the same answer for
            # 1/577th of the cost: 2,494 ms -> 4 ms at 25k articles / 17.4k event rows.
            want = str(country).strip().upper()
            conds.append(FeedArticle.canonical_url.in_(
                select(ArticleEventLocation.canonical_url)
                .where(ArticleEventLocation.country == want)))
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
                             date_from=None, date_to=None, source=None, country=None,
                             sort="newest", pagination=None, include_provisional: bool = True,
                             exclude_publishers=None, include_shadow: bool = False):
        """Search the catalog directly, in SQL. Returns ``(rows, total)`` — ``rows`` are paginated
        FeedArticle-row dicts, ``total`` the match count before pagination. All filtering / sorting /
        paging happen in the database (index-backed); it never touches the recommendation engine.
        ``pagination`` is a :class:`pagination.Pagination` (defaults to offset paging).
        ``include_provisional=False`` (the Discover surface only) hides extension-created articles that
        haven't been promoted yet; Search/Stories/export keep the default and see everything.

        ``exclude_publishers`` is a set of LOWER-CASED publisher strings to leave out — the clustering
        corpus's tier prefilter (``corpus.sql_exclusions``), and nothing else passes it. It exists so
        the row cap bounds **Tier A** rather than the mixture: applied here it runs before ``LIMIT``,
        so an excluded row never consumes cap. Empty or ``None`` adds no term at all, which is what
        keeps every other caller — Search, Discover, export — byte-identical.

        ``include_shadow`` is **False by default, and that default is the point** (M5,
        `docs/SCALE_ROADMAP.md`). A shadow outlet is one being observed before evaluation, and the
        corpus contract says it is surfaced nowhere. This method is the single path every reader
        surface funnels through — Search, Discover, publisher profiles, facets — so the rule lives
        here rather than at seven call sites.

        The store is otherwise policy-free and this is a deliberate exception. The alternative was an
        explicit exclusion at each caller, and that is precisely how shadow came to be half
        implemented in the first place: it was enforced in ``story_service._fetch`` alone, leaving
        every shadow article fully searchable while the code documented it as "surfaced nowhere".
        Defaulting to exclusion means a NEW reader surface is safe the day it is written, and the
        failure mode of forgetting the flag is "the evaluation harness cannot see what it evaluates"
        — loud and immediate — rather than "unvetted sources reached readers" — silent.
        Pass ``include_shadow=True`` from evaluation and audit paths that must see the lane."""
        from pagination import OffsetPagination
        pg = pagination or OffsetPagination()
        if not include_shadow:
            # Local import: `corpus` reaches the outlet registry, and a store built for a test or a
            # migration should not pay for loading it when nothing is in shadow.
            import corpus
            shadow = corpus.shadow_exclusions()
            if shadow:
                exclude_publishers = frozenset(exclude_publishers or ()) | shadow
        conds = self._search_conditions(q=q, publisher=publisher, lean=lean, topic=topic,
                                         date_from=date_from, date_to=date_to, source=source,
                                         country=country)
        if exclude_publishers:
            # NULL-safe by construction. `lower(NULL) NOT IN (...)` evaluates to NULL, not TRUE, so
            # a bare NOT IN silently drops every row with no publisher — a filter that removes rows
            # it was never asked about. The explicit IS NULL arm is what keeps them.
            conds = list(conds) + [or_(
                FeedArticle.publisher.is_(None),
                func.lower(FeedArticle.publisher).notin_(sorted(exclude_publishers)))]
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

    def feed_article_facets(self, include_provisional: bool = True,
                            include_shadow: bool = False) -> dict:
        """Distinct publishers + topics (categories) across the catalog, for filter dropdowns.
        ``include_provisional=False`` (Discover) keeps the facet counts consistent with what that
        surface actually lists — unpromoted extension-created articles are excluded.

        ``include_shadow=False`` (the default) applies the same rule to the shadow lane, and a
        facet list is where a half-enforced boundary shows first: a shadow publisher left in the
        dropdown names an outlet the reader can never see results from, which is worse than hiding
        it — it advertises the lane and then returns nothing."""
        cond = or_(FeedArticle.article_state.is_(None), FeedArticle.article_state != "provisional")
        with self.session() as s:
            pq, cq = select(FeedArticle.publisher).distinct(), select(self._category_expr()).distinct()
            if not include_provisional:
                pq, cq = pq.where(cond), cq.where(cond)
            pubs = [p for (p,) in s.execute(pq).all() if p]
            cats = [c for (c,) in s.execute(cq).all() if c]
        if not include_shadow:
            import corpus
            shadow = corpus.shadow_exclusions()
            if shadow:
                pubs = [p for p in pubs if p.strip().lower() not in shadow]
        return {"publishers": sorted(set(pubs)), "topics": sorted(set(cats))}

    def catalog_topic_counts(self, include_provisional: bool = False) -> dict:
        """Per-category article counts across the WHOLE catalog + the categorized total — the
        baseline the publisher blindspot comparison measures against (same provisional exclusion
        as Discover). Uncategorized rows are absent from both counts and total, so publisher and
        catalog shares use the same denominator convention (categorized articles)."""
        q = select(self._category_expr(), func.count()).group_by(self._category_expr())
        if not include_provisional:
            q = q.where(or_(FeedArticle.article_state.is_(None),
                            FeedArticle.article_state != "provisional"))
        with self.session() as s:
            rows = s.execute(q).all()
        topics = {str(c).strip(): int(n) for c, n in rows if c and str(c).strip()}
        return {"topics": topics, "total": sum(topics.values())}

    def publisher_catalog_stats(self, publisher: str) -> "dict | None":
        """Counted catalog facts for ONE publisher (Publisher Intelligence): volume + observed
        window, per-topic / per-language / per-host / per-event-country counts, and tone splits
        computed ONLY over rows that actually carry the signal — each with its own ``n``; a
        missing signal is excluded, never defaulted (the serializer's fail-honest rule, L2.2).
        Publisher match is case-insensitive (the catalog search filter's semantics); provisional
        (uncorroborated extension-created) rows are excluded, like Discover. Returns ``None``
        when the catalog holds no rows for the name — absence, not an empty profile."""
        cond = and_(func.lower(FeedArticle.publisher) == (publisher or "").strip().lower(),
                    or_(FeedArticle.article_state.is_(None), FeedArticle.article_state != "provisional"))
        with self.session() as s:
            rows = s.execute(select(FeedArticle.canonical_url, FeedArticle.url,
                                    FeedArticle.publisher, FeedArticle.published_at,
                                    FeedArticle.language, FeedArticle.scored)
                             .where(cond)).all()
            if not rows:
                return None
            urls = [r[0] for r in rows]
            event_countries: dict = {}
            for i in range(0, len(urls), 500):     # SQLite bound-parameter limit, as elsewhere
                for c, n in s.execute(
                        select(ArticleEventLocation.country,
                               func.count(func.distinct(ArticleEventLocation.canonical_url)))
                        .where(ArticleEventLocation.canonical_url.in_(urls[i:i + 500]))
                        .group_by(ArticleEventLocation.country)).all():
                    event_countries[c] = event_countries.get(c, 0) + int(n)
        topics: dict = {}
        languages: dict = {}
        hosts: dict = {}
        registers = {"reporting": 0, "opinion": 0, "mixed": 0}
        register_n = 0
        emotion_sum = {k: 0.0 for k in ("fear", "outrage", "analysis", "positive", "neutral")}
        emotion_n = 0
        first = last = None
        for _cu, url, _pub, published, lang, scored_json in rows:
            if published:
                first = published if first is None or published < first else first
                last = published if last is None or published > last else last
            try:
                scored = json.loads(scored_json) or {}
            except (TypeError, ValueError):
                scored = {}
            cat = str(scored.get("category") or "").strip()
            if cat:
                topics[cat] = topics.get(cat, 0) + 1
            if lang:
                languages[lang] = languages.get(lang, 0) + 1
            host = _url_host(url)
            if host:
                hosts[host] = hosts.get(host, 0) + 1
            bucket = _register_bucket(scored.get("register"))
            if bucket:
                registers[bucket] += 1
                register_n += 1
            emo = scored.get("emotion")
            if isinstance(emo, dict) and emo:
                vals = {k: emo.get(k) for k in emotion_sum}
                if all(isinstance(v, (int, float)) and math.isfinite(v) for v in vals.values()):
                    for k, v in vals.items():
                        emotion_sum[k] += float(v)
                    emotion_n += 1

        def _counted(d: dict) -> list:
            return [{"label": k, "count": v}
                    for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))]

        emotion = ({**{k: round(v / emotion_n, 4) for k, v in emotion_sum.items()}, "n": emotion_n}
                   if emotion_n else None)
        return {"publisher": rows[0][2], "total": len(rows),
                "firstSeen": first, "lastSeen": last,
                "topics": _counted(topics), "languages": _counted(languages),
                "hosts": _counted(hosts), "eventCountries": _counted(event_countries),
                "registers": {**registers, "n": register_n} if register_n else None,
                "emotion": emotion}

    # -- publisher metadata cache (Wikipedia/Wikimedia enrichment) --------------------
    @staticmethod
    def publisher_key(name: str) -> str:
        """The cache key for a publisher name: casefolded, whitespace-collapsed.

        Deliberately NOT the aggressive alphanumeric squash the registry uses for alias matching —
        that maps distinct outlets onto one key ("The Hill" and "TheHill" is a fair merge, but the
        squash is also how unrelated names collide). Here a wrong merge would show one publisher's
        facts on another's page, so the key stays conservative and the registry does the aliasing
        upstream: callers resolve to a canonical name FIRST, then key on it."""
        return " ".join((name or "").split()).casefold()

    @staticmethod
    def _publisher_metadata_row(r: "PublisherMetadata") -> dict:
        return {
            "publisher": r.publisher, "status": r.status, "reason": r.reason,
            "source": r.source,
            "wikidataId": r.wikidata_id, "wikipediaTitle": r.wikipedia_title,
            "wikipediaUrl": r.wikipedia_url, "description": r.description,
            "founded": r.founded, "headquarters": r.headquarters, "country": r.country,
            "website": r.website, "parent": r.parent, "logo": r.logo,
            "logoSource": r.logo_source, "error": r.error,
            "fetchedAt": r.fetched_at.isoformat() if r.fetched_at else None,
        }

    def upsert_publisher_metadata(self, publisher: str, *, status: str = "ok",
                                  source: "str | None" = None, at: "datetime | None" = None,
                                  **fields) -> dict:
        """Write one lookup result. Idempotent by construction: the key is derived from the name, so
        re-running an enrichment overwrites its own row instead of accumulating duplicates.

        Every call REPLACES the fact columns, including with None. That is deliberate — if Wikidata
        drops a claim, the cache must drop it too, or the page would keep serving a fact its source
        no longer asserts. Curated registry values are unaffected: they live in the registry and are
        merged on read, never written here."""
        key = self.publisher_key(publisher)
        cols = ("reason", "wikidata_id", "wikipedia_title", "wikipedia_url", "description",
                "founded", "headquarters", "country", "website", "parent", "logo", "logo_source",
                "error")
        unknown = set(fields) - set(cols)
        if unknown:
            raise ValueError(f"unknown publisher metadata fields: {sorted(unknown)}")
        with self.session() as s:
            row = s.get(PublisherMetadata, key)
            if row is None:
                row = PublisherMetadata(publisher_key=key, publisher=publisher)
                s.add(row)
            row.publisher = publisher
            row.status = status
            row.source = source
            for c in cols:
                setattr(row, c, fields.get(c))
            row.fetched_at = at or _utcnow()
            s.commit()
            return self._publisher_metadata_row(row)

    def publisher_metadata(self, publisher: str) -> "dict | None":
        """The cached row for one publisher, or None when never looked up."""
        with self.session() as s:
            row = s.get(PublisherMetadata, self.publisher_key(publisher))
            return self._publisher_metadata_row(row) if row is not None else None

    def publisher_metadata_many(self, publishers) -> dict:
        """``{publisher_key: row}`` for several names in one query — the profile page and the
        enricher both need bulk reads, and N round-trips per cycle is the thing to avoid."""
        keys = {self.publisher_key(p) for p in publishers if (p or "").strip()}
        if not keys:
            return {}
        with self.session() as s:
            rows = s.scalars(select(PublisherMetadata)
                             .where(PublisherMetadata.publisher_key.in_(keys))).all()
            return {r.publisher_key: self._publisher_metadata_row(r) for r in rows}

    def publisher_metadata_stats(self) -> dict:
        """Counts by status — the operational view: how much of the catalog is enriched, and how
        many outlets are sitting in ``ambiguous`` waiting for a human."""
        with self.session() as s:
            rows = s.execute(select(PublisherMetadata.status, func.count())
                             .group_by(PublisherMetadata.status)).all()
        counts = {str(k): int(n) for k, n in rows}
        return {"total": sum(counts.values()), "byStatus": counts}

    def all_feed_articles_for_lean_backfill(self) -> list:
        """``canonicalUrl``, ``publisher`` and raw ``scored`` for every catalog article.

        Deliberately not filtered by the story window: a lean correction should reach the whole
        catalog, since search, history and the publisher pages read these rows too and an article
        outside today's clustering window is still served."""
        with self.session() as s:
            return [{"canonicalUrl": u, "publisher": p, "scored": sc}
                    for u, p, sc in s.execute(select(
                        FeedArticle.canonical_url, FeedArticle.publisher,
                        FeedArticle.scored)).all()]

    def apply_lean_backfill(self, updates: list) -> int:
        """Rewrite ONLY the lean fields inside each article's stored ``scored`` JSON.

        Read-modify-write per row rather than a JSON SQL function, so the blob is re-serialised
        through the same sanitiser every other write uses and cannot acquire a non-finite float or
        an invalid document. Everything except the lean is preserved byte-for-byte: category,
        register, emotion and confidence were measured per article, while the lean is a property of
        the outlet and is the only field the registry owns."""
        if not updates:
            return 0
        wanted = dict(updates)
        n = 0
        with self.session() as s:
            for row in s.execute(select(FeedArticle).where(
                    FeedArticle.canonical_url.in_(list(wanted)))).scalars():
                try:
                    scored = json.loads(row.scored)
                except (TypeError, ValueError):
                    continue
                scored["lean"] = wanted[row.canonical_url]
                row.scored = _dumps_scored(scored)
                n += 1
        return n

    def story_member_ids(self) -> dict:
        """``url -> story_id`` for every article the last build served. The whole table: it is
        bounded by the clustering window, so this is thousands of rows, and loading it once beats
        an IN-clause over every url in the current build."""
        with self.session() as s:
            return {u: sid for u, sid in s.execute(
                select(StoryMember.url, StoryMember.story_id)).all()}

    def replace_story_members(self, mapping: dict) -> int:
        """Replace the whole url -> story_id map with the current build's.

        Wholesale rather than incremental, and that IS the pruning: a url outside the current
        window is not being served, so remembering which story it used to belong to buys nothing
        and would grow the table without bound. One transaction, so a crash mid-write leaves the
        previous map intact rather than a half-updated one — ids would churn either way, but a
        torn map would churn them unpredictably."""
        with self.session() as s:
            s.execute(delete(StoryMember))
            if mapping:
                now = _utcnow()
                s.bulk_save_objects([StoryMember(url=u, story_id=sid, updated_at=now)
                                     for u, sid in mapping.items()])
        return len(mapping)

    def catalog_publishers(self, *, limit: "int | None" = None) -> list:
        """Distinct publisher names in the catalog, most-published first — the enrichment worklist.
        Busiest outlets first so a bounded per-cycle budget is spent where readers will see it."""
        q = (select(FeedArticle.publisher, func.count().label("n"))
             .where(FeedArticle.publisher.is_not(None))
             .group_by(FeedArticle.publisher)
             .order_by(func.count().desc(), FeedArticle.publisher))
        if limit:
            q = q.limit(limit)
        with self.session() as s:
            return [{"publisher": p, "articles": int(n)} for p, n in s.execute(q).all() if p]

    def publisher_first_seen(self, publishers=None) -> dict:
        """``{lower-cased publisher: ISO timestamp}`` — ``MIN(created_at)`` per outlet, over the
        **WHOLE catalog**, never a window.

        The whole-catalog part is the point, and a production run is why it exists (M8,
        `docs/SCALE_ROADMAP.md` Part 10). ``audit_shadow_cohort`` first derived "how long have we
        been seeing this outlet" from the rows it had already fetched — but those rows come from
        ``story_service._fetch``, which is bounded to a **6-day** window. So the observation span
        could never exceed 6 days, the 14-day gate could never be satisfied, and every outlet
        evaluated for the rest of time would return ``INSUFFICIENT DATA``. Measured on production:
        `sportskeeda.com`, 989 articles, reported ``observed 6.0d`` — exactly the window, because
        that is all the query could see.

        The ceiling that remains is **retention**, and it is honest rather than hidden: an outlet
        cannot be observed for longer than its oldest surviving row. With age-based retention off
        (the shipped default) that is the count cap, which currently reaches back months.

        **The ceiling is per-outlet, not global, and the reason is a column mismatch worth knowing.**
        ``corpus_health.plan_retention`` orders candidates by ``publishedAt`` (falling back to
        ``fetchedAt``); this method measures ``created_at``. Those are different orderings, so
        retention can remove the rows carrying an outlet's oldest ``created_at`` while the catalog's
        *global* ``MIN(created_at)`` — :meth:`catalog_first_seen` — does not move at all. Observed on
        production: sportskeeda's first-seen advanced 50 minutes between two runs 18 minutes apart
        while the global floor stayed byte-identical. So a floor comparison shows whether an outlet
        is pinned to the oldest surviving row; it does **not** prove the outlet's own history is
        untrimmed. The whole-catalog row count reported beside it is what answers that.

        ``publishers`` optionally narrows to a set of lower-cased names — the cohort, so a run over
        20 outlets does not aggregate the whole catalog."""
        q = (select(FeedArticle.publisher, func.min(FeedArticle.created_at))
             .where(FeedArticle.publisher.is_not(None))
             .group_by(FeedArticle.publisher))
        if publishers is not None:
            wanted = {p.strip().lower() for p in publishers if p and p.strip()}
            if not wanted:
                return {}
            q = q.where(func.lower(FeedArticle.publisher).in_(sorted(wanted)))
        with self.session() as s:
            out = {}
            for pub, first in s.execute(q).all():
                if not pub or first is None:
                    continue
                key = pub.strip().lower()
                iso = first.isoformat() if hasattr(first, "isoformat") else str(first)
                # An outlet can appear under several capitalisations; keep the EARLIEST.
                if key not in out or iso < out[key]:
                    out[key] = iso
            return out

    def catalog_first_seen(self) -> "str | None":
        """The oldest surviving ``created_at`` in the catalog, or ``None`` when empty.

        The **retention floor**, and it exists to disambiguate :meth:`publisher_first_seen`. An
        outlet whose first-seen equals this has not been observed for that long — it has merely not
        been trimmed yet, and its true first-seen is unknowable from what we still hold. Reporting a
        floor-pinned span as an observation would be the same class of error as reporting a fetch
        window as one (M8, `docs/SCALE_ROADMAP.md` Part 10)."""
        with self.session() as s:
            first = s.scalar(select(func.min(FeedArticle.created_at)))
        if first is None:
            return None
        return first.isoformat() if hasattr(first, "isoformat") else str(first)

    def publisher_last_seen(self, publishers=None) -> dict:
        """``{lower-cased publisher: ISO}`` — ``MAX(created_at)`` per outlet, the silence signal.

        The mirror of :meth:`publisher_first_seen`, and ``created_at`` for the same reason: a
        backfilling provider inserting a month-old article says we heard from the source *today*,
        which is what dormancy is asking about. ``published_at`` would call that source silent."""
        q = (select(FeedArticle.publisher, func.max(FeedArticle.created_at))
             .where(FeedArticle.publisher.is_not(None))
             .group_by(FeedArticle.publisher))
        if publishers is not None:
            wanted = {p.strip().lower() for p in publishers if p and p.strip()}
            if not wanted:
                return {}
            q = q.where(func.lower(FeedArticle.publisher).in_(sorted(wanted)))
        with self.session() as s:
            out = {}
            for pub, last in s.execute(q).all():
                if not pub or last is None:
                    continue
                key = pub.strip().lower()
                iso = last.isoformat() if hasattr(last, "isoformat") else str(last)
                if key not in out or iso > out[key]:        # several spellings: the LATEST
                    out[key] = iso
            return out

    # -- source lifecycle (M9, docs/SCALE_ROADMAP.md) ---------------------------------
    def source_lifecycle(self, identity: str) -> "dict | None":
        """One outlet's lifecycle row, or ``None`` if it has never been evaluated."""
        with self.session() as s:
            row = s.get(SourceLifecycle, identity)
            return self._lifecycle_row(row) if row else None

    @staticmethod
    def _lifecycle_row(r: "SourceLifecycle") -> dict:
        return {"identity": r.identity, "state": r.state, "since": r.since,
                "firstObserved": r.first_observed, "lastSeen": r.last_seen,
                "lastVerdict": r.last_verdict, "lastTarget": r.last_target,
                "streak": int(r.streak or 0), "lastEvaluatedAt": r.last_evaluated_at,
                "evidence": json.loads(r.evidence or "{}"), "reason": r.reason}

    def record_source_evaluation(self, identity: str, *, target: "str | None", verdict: str,
                                 evidence: "dict | None" = None, at: "str | None" = None,
                                 first_observed: "str | None" = None,
                                 last_seen: "str | None" = None,
                                 initial_state: str = "shadow",
                                 min_spacing_days: float = 0.0) -> dict:
        """Record one evaluation and return the updated row. Idempotent per identity, not per run.

        **``streak`` is maintained here because hysteresis needs memory and a run is a fresh
        process**, but the arithmetic itself lives in `source_lifecycle.next_streak` so there is one
        definition rather than one here and another in the runner's dry-run path.

        ``min_spacing_days`` is the interval below which a second evaluation does not count — two
        runs minutes apart evaluate the same corpus and confirm nothing. See `next_streak`; the
        returned row carries ``held`` when a sample was redundant. ``0.0`` disables the check, which
        is what unit tests of the streak arithmetic itself want.

        ``first_observed`` is written on first sight and then only ever moved **earlier**, never
        later. That is the retention-erosion fix: `MIN(created_at)` shrinks an outlet's apparent
        history as its oldest rows are trimmed, and an observation window that shortens on its own
        would let a long-observed outlet fall back below the evaluation gate."""
        # Local import: policy, and a store built for a migration should not pay for loading it.
        # The same reason `search_feed_articles` reaches for `corpus` locally.
        import source_lifecycle as _sl
        now = at or _utcnow().isoformat()
        with self.session() as s:
            row = s.get(SourceLifecycle, identity)
            if row is None:
                row = SourceLifecycle(identity=identity, state=initial_state, since=now,
                                      first_observed=first_observed or now)
                s.add(row)
            elif first_observed and first_observed < (row.first_observed or first_observed):
                row.first_observed = first_observed         # earlier only
            gap = _iso_gap_days(row.last_evaluated_at, now)
            row.streak, held = _sl.next_streak(row.last_target, row.streak, gap, target,
                                               min_spacing_days=min_spacing_days)
            row.last_target = target
            row.last_verdict = verdict
            row.last_evaluated_at = now
            if last_seen:
                row.last_seen = last_seen
            row.evidence = json.dumps(evidence or {}, sort_keys=True, default=str)
            s.flush()
            return dict(self._lifecycle_row(row), held=held, gapDays=gap)

    def apply_source_transition(self, identity: str, *, to: str, reason: str,
                                automatic: bool = False, applied: bool = False,
                                evidence: "dict | None" = None,
                                at: "str | None" = None) -> dict:
        """Move an outlet to ``to`` and append an event. Returns the updated row.

        ``applied`` says whether the serving configuration was actually changed — which M9 never
        does itself. A transition recorded with ``applied=False`` is a *decision*, and the emitted
        config is what a human deploys to make it real. Keeping the two separate is what lets the
        ledger show a decision that was proposed and never shipped, instead of claiming a state the
        running system is not in."""
        now = at or _utcnow().isoformat()
        blob = json.dumps(evidence or {}, sort_keys=True, default=str)
        with self.session() as s:
            row = s.get(SourceLifecycle, identity)
            frm = row.state if row else "shadow"
            if row is None:
                row = SourceLifecycle(identity=identity, state=to, since=now, first_observed=now)
                s.add(row)
            else:
                row.state = to
                row.since = now
            row.reason = reason
            s.add(SourceLifecycleEvent(identity=identity, frm=frm, to=to, at=now,
                                       automatic=automatic, applied=applied,
                                       reason=reason, evidence=blob))
            s.flush()
            return self._lifecycle_row(row)

    def source_lifecycle_states(self) -> dict:
        """``{identity: row}`` for every outlet the ledger knows."""
        with self.session() as s:
            return {r.identity: self._lifecycle_row(r)
                    for r in s.execute(select(SourceLifecycle)).scalars()}

    def source_lifecycle_events(self, identity: "str | None" = None, *, limit: int = 200) -> list:
        """The append-only ledger, newest first. Never overwritten — see `SourceLifecycleEvent`."""
        q = select(SourceLifecycleEvent).order_by(SourceLifecycleEvent.id.desc()).limit(limit)
        if identity:
            q = q.where(SourceLifecycleEvent.identity == identity)
        with self.session() as s:
            return [{"id": e.id, "identity": e.identity, "from": e.frm, "to": e.to, "at": e.at,
                     "automatic": bool(e.automatic), "applied": bool(e.applied),
                     "reason": e.reason, "evidence": json.loads(e.evidence or "{}")}
                    for e in s.execute(q).scalars()]

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

    def feed_schedule_state(self, feed_url: str) -> dict:
        """One feed's persisted scheduling state, or empty defaults when it has never been polled.

        Returns a plain dict rather than a ``feed_schedule.FeedState`` so ``store`` keeps no import
        of the policy module — the same direction of dependency every other side table observes."""
        with self.session() as s:
            row = s.get(FeedHealth, feed_url)
            if row is None:
                return {"etag": None, "last_modified": None, "content_sha": None,
                        "next_due_at": None, "interval_s": None, "consecutive_failures": 0}
            return {"etag": row.etag, "last_modified": row.last_modified,
                    "content_sha": row.content_sha, "next_due_at": row.next_due_at,
                    "interval_s": row.interval_s,
                    "consecutive_failures": int(row.consecutive_failures or 0)}

    def record_feed_schedule(self, feed_url: str, *, etag=None, last_modified=None,
                             content_sha=None, next_due_at=None, interval_s=None) -> None:
        """Persist one feed's scheduling state. Creates the health row if this feed is new, so the
        scheduler can run before a health record exists (a feed skipped on its first cycle would
        otherwise never acquire state and would be re-polled forever)."""
        with self.session() as s:
            row = s.get(FeedHealth, feed_url)
            if row is None:
                row = FeedHealth(feed_url=feed_url, healthy=True, consecutive_failures=0,
                                 total_polls=0, total_ok=0, total_failed=0,
                                 imported=0, duplicate=0, rejected=0, missing_metadata=0)
                s.add(row)
            row.etag = etag
            row.last_modified = last_modified
            row.content_sha = content_sha
            row.next_due_at = next_due_at
            row.interval_s = interval_s
            row.updated_at = _utcnow()

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
        click-through that Open-Mindedness ranks; ``None`` when none have been surfaced.

        Two indexed COUNTs, deliberately not a row fetch. This sits inside the personal-model
        cache key — paid three times per recommendations request — and the production probe
        measured the previous materialise-then-``len()`` shape at 40–170 ms per call once an
        active reader's ``rec_events`` had grown; the rows were never used, only their number."""
        with self.session() as s:
            cross = and_(RecEvent.user_id == user_id, RecEvent.cross_cutting.is_(True))
            shown = int(s.scalar(select(func.count()).select_from(RecEvent)
                                 .where(cross)) or 0)
            opened = int(s.scalar(select(func.count()).select_from(RecEvent)
                                  .where(cross, RecEvent.opened_at.is_not(None))) or 0)
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

    def rec_events_state(self, user_id: int, since: "str | None" = None) -> list:
        """Per-article reception state for the reader's OWN next feed request —
        ``{articleId, opened}`` rows, optionally only those last surfaced at/after ``since`` (an ISO
        string; ``shown_at`` is refreshed on re-surface, so the filter reads "still recently on
        screen"). The read half of the repetition-decay loop (``rec_context.py``, Tier 1 of the
        X-audit roadmap): the serve path has always *written* these rows; this projection is what
        lets the next serve stop re-surfacing a card the reader has already scrolled past.
        Read-only; invokes no recommender."""
        with self.session() as s:
            q = select(RecEvent).where(RecEvent.user_id == user_id)
            if since is not None:
                q = q.where(RecEvent.shown_at >= since)
            rows = s.scalars(q.order_by(RecEvent.id)).all()
            return [{"articleId": r.article_id, "opened": r.opened_at is not None} for r in rows]

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

    # ------------------------------------------------------------------ #
    # Event-identity verdicts (event_identity) — the banded judge's memory.
    # ------------------------------------------------------------------ #
    def event_verdicts(self) -> dict:
        """``pair_key -> verdict`` for every MODEL-judged pair — the build's input dict. Only
        ``source == "model"`` rows count: api-error rows are retried, never trusted."""
        with self.session() as s:
            rows = s.execute(select(EventVerdict.pair_key, EventVerdict.verdict)
                             .where(EventVerdict.source == "model",
                                    EventVerdict.verdict.is_not(None))).all()
            return {k: v for k, v in rows}

    def enqueue_event_pairs(self, pairs: "list[dict]") -> int:
        """Insert PENDING rows for band pairs a build emitted. Existing keys are left alone —
        the FIRST asking build's snapshot is what gets judged, and a judged row is never
        re-opened by a later build asking the same question."""
        if not pairs:
            return 0
        now = _utcnow().isoformat()
        created = 0
        with self.session() as s:
            for p in pairs:
                if s.get(EventVerdict, p["pair_key"]) is not None:
                    continue
                s.add(EventVerdict(
                    pair_key=p["pair_key"], url_a=str(p.get("url_a") or ""),
                    url_b=str(p.get("url_b") or ""),
                    title_a=str(p.get("title_a") or ""), dek_a=str(p.get("dek_a") or ""),
                    published_a=str(p.get("published_a") or ""),
                    title_b=str(p.get("title_b") or ""), dek_b=str(p.get("dek_b") or ""),
                    published_b=str(p.get("published_b") or ""),
                    verdict=None, source="", first_seen=now))
                created += 1
        return created

    def pending_event_pairs(self, limit: int = 120,
                            retry_after_hours: float = 1.0) -> "list[dict]":
        """The worker's queue: never-judged rows first (oldest first), then api-error rows whose
        last attempt is older than the cooldown — transport trouble is retried, not trusted."""
        cutoff = (_utcnow() - timedelta(hours=retry_after_hours)).isoformat()
        with self.session() as s:
            rows = s.scalars(
                select(EventVerdict)
                .where(or_(EventVerdict.verdict.is_(None),
                           and_(EventVerdict.source == "api-error",
                                EventVerdict.judged_at < cutoff)))
                .order_by(EventVerdict.first_seen).limit(limit)).all()
            return [{"pair_key": r.pair_key, "url_a": r.url_a, "url_b": r.url_b,
                     "title_a": r.title_a, "dek_a": r.dek_a, "published_a": r.published_a,
                     "title_b": r.title_b, "dek_b": r.dek_b, "published_b": r.published_b}
                    for r in rows]

    def record_event_verdict(self, pair_key: str, verdict: str, *, source: str,
                             model: str = "") -> bool:
        """Persist one judgment onto its queued row. Unknown key -> False (the queue is the only
        writer of rows; a verdict without a question is not recorded)."""
        with self.session() as s:
            row = s.get(EventVerdict, pair_key)
            if row is None:
                return False
            row.verdict, row.source, row.model = verdict, source, model
            row.judged_at = _utcnow().isoformat()
            return True

    def remove_recommendation_feedback(self, user_id: int, article_id: str,
                                       feedback: "str | None" = None) -> int:
        """Delete the reader's feedback on one article — one type, or (``feedback=None``) every
        type they gave it. Returns the number of rows removed (0 = nothing was recorded, which is
        a fine answer, not an error). This is the "undo" the Tier-2 visible-consequence UI
        promises: a consequence a reader can see but not retract is surveillance, so removal is
        as first-class as recording. Scoped strictly to ``user_id`` — one reader can never clear
        another's signals."""
        with self.session() as s:
            q = select(RecFeedback).where(RecFeedback.user_id == user_id,
                                          RecFeedback.article_id == str(article_id))
            if feedback is not None:
                q = q.where(RecFeedback.feedback == feedback)
            rows = s.scalars(q).all()
            for row in rows:
                s.delete(row)
            return len(rows)

    def recommendation_feedback_counts(self, user_id: int) -> dict:
        """The reader's article-recommendation feedback tallied by type — ``{like, dislike, ignore,
        read_later}`` (one grouped count query, missing types default to 0). The improvement ranker
        (RC2.4) reads this as a cheap global receptivity prior; it drives no recommender or metric."""
        counts = {t: 0 for t in RECOMMENDATION_FEEDBACK_TYPES}
        with self.session() as s:
            rows = s.execute(
                select(RecFeedback.feedback, func.count())
                .where(RecFeedback.user_id == user_id)
                .group_by(RecFeedback.feedback)).all()
        for feedback, n in rows:
            if feedback in counts:
                counts[feedback] = int(n)
        return counts

    # -- experiment assignments (Tier 2 cohort harness) -------------------
    def record_experiment_assignment(self, user_id: int, experiment: str, cohort: str) -> bool:
        """Record a reader's arm in one experiment, write-once: the first call creates the row
        and returns ``True``; every later call — same or different cohort — is a no-op returning
        ``False``, because an assignment that could drift after the fact would be worthless for
        audit (the hash is deterministic, so a "different" cohort here could only mean the env
        spec changed mid-experiment — the ORIGINAL arm is the one the reader actually lived)."""
        with self.session() as s:
            row = s.scalar(select(ExperimentAssignment).where(
                ExperimentAssignment.user_id == user_id,
                ExperimentAssignment.experiment == str(experiment)))
            if row is not None:
                return False
            s.add(ExperimentAssignment(user_id=user_id, experiment=str(experiment),
                                       cohort=str(cohort),
                                       assigned_at=_utcnow().isoformat()))
            return True

    def experiment_assignments(self, experiment: "str | None" = None) -> list:
        """The recorded assignments — all of them, or one experiment's — as ``{userId,
        experiment, cohort, assignedAt}``, oldest first. Read-only, for analysis/audit."""
        with self.session() as s:
            q = select(ExperimentAssignment)
            if experiment is not None:
                q = q.where(ExperimentAssignment.experiment == str(experiment))
            rows = s.scalars(q.order_by(ExperimentAssignment.id)).all()
            return [{"userId": r.user_id, "experiment": r.experiment, "cohort": r.cohort,
                     "assignedAt": r.assigned_at} for r in rows]

    # -- product analytics events (PA1) ---------------------------------
    def record_analytics_events(self, events: "list[dict]") -> int:
        """Persist a batch of already-normalized analytics events (PA1). Each dict carries
        ``event`` + optional ``user_id / anon_id / session_id / props(dict) / client_ts /
        server_ts / request_id`` (the sink stamps the authoritative fields). Returns the count
        written. Measurement only — no consumer of users/reads/reports/recs reads this table."""
        if not events:
            return 0
        with self.session() as s:
            for e in events:
                props = e.get("props")
                s.add(AnalyticsEvent(
                    event=e["event"],
                    user_id=e.get("user_id"),
                    anon_id=e.get("anon_id"),
                    session_id=e.get("session_id"),
                    props=json.dumps(props, default=str) if props else None,
                    client_ts=e.get("client_ts"),
                    server_ts=e.get("server_ts") or _utcnow().isoformat(),
                    request_id=e.get("request_id"),
                ))
        return len(events)

    def list_analytics_events(self, *, since: "str | None" = None, limit: int = 100000) -> list:
        """Analytics events as plain dicts for :mod:`product_analytics` — oldest first, props parsed
        back to a dict. ``since`` (ISO) bounds by ``server_ts``; ``limit`` caps the scan. Read-only."""
        with self.session() as s:
            q = select(AnalyticsEvent).order_by(AnalyticsEvent.id)
            if since:
                q = q.where(AnalyticsEvent.server_ts >= since)
            rows = s.scalars(q.limit(limit)).all()
            out = []
            for r in rows:
                try:
                    props = json.loads(r.props) if r.props else {}
                except (ValueError, TypeError):
                    props = {}
                out.append({"event": r.event, "userId": r.user_id, "anonId": r.anon_id,
                            "sessionId": r.session_id, "props": props,
                            "clientTs": r.client_ts, "serverTs": r.server_ts})
            return out

    def count_analytics_events(self) -> int:
        """Total analytics events recorded (a cheap dashboard/health counter)."""
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(AnalyticsEvent)) or 0)

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
                    try:
                        # Savepoint per insert: two concurrent report requests for the same reader can
                        # both miss the SELECT above and race to INSERT the same (user_id, rec_key). The
                        # UNIQUE constraint is the arbiter; the loser's savepoint rolls back and we then
                        # re-fetch the winner's row and update it, so the write is never lost.
                        with s.begin_nested():
                            r = ImprovementLifecycle(user_id=user_id, rec_key=rk,
                                                     metric=str(row.get("metric") or ""),
                                                     state=str(row.get("state") or "generated"))
                            s.add(r)
                            s.flush()
                    except IntegrityError:
                        r = s.scalar(select(ImprovementLifecycle).where(
                            ImprovementLifecycle.user_id == user_id,
                            ImprovementLifecycle.rec_key == rk))
                        if r is None:
                            continue
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
                try:
                    with s.begin_nested():          # isolate a concurrent-insert race (see save_ above)
                        r = ImprovementLifecycle(user_id=user_id, rec_key=str(rec_key),
                                                 metric=str(metric or ""), state="generated",
                                                 generated_at=stamp)
                        s.add(r)
                        s.flush()
                except IntegrityError:
                    r = s.scalar(select(ImprovementLifecycle).where(
                        ImprovementLifecycle.user_id == user_id,
                        ImprovementLifecycle.rec_key == str(rec_key)))
                    created = False
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

    def unseen_notification(self, user_id: int, kind: str) -> "dict | None":
        """The user's oldest UNSEEN notification of ``kind`` (``{"id", "body"}``), or ``None``.
        Read-only. Lets the delivery boundary keep at most ONE outstanding state alert per kind
        instead of minting a fresh row every evaluation period."""
        with self.session() as s:
            row = s.scalar(select(Notification)
                           .where(Notification.user_id == user_id, Notification.kind == kind,
                                  Notification.seen_at.is_(None))
                           .order_by(Notification.id.asc()).limit(1))
            if row is None:
                return None
            try:
                body = dict(json.loads(row.body))
            except (TypeError, ValueError):
                body = {}
            return {"id": row.id, "body": body}

    def refresh_notification(self, user_id: int, notification_id: int, body: dict,
                             dedupe_key: "str | None" = None) -> bool:
        """Replace an UNSEEN notification's stored body in place — the payload of a still-true state
        alert (e.g. the current waiting-recommendation count) without creating a second row. Never
        touches a seen row (that is history) or another user's row.

        ``dedupe_key`` re-stamps the idempotency column too, so the ledger keeps describing what was
        actually delivered: without it, the refreshed key is absent from the ledger and the alert
        would re-fire the instant the reader dismissed it. A rare UNIQUE collision (the same key
        already recorded on another row) leaves the key as-is and still refreshes the body — the
        body is the part the reader sees. Returns whether it changed."""
        with self.session() as s:
            row = s.scalar(select(Notification).where(Notification.id == notification_id,
                                                      Notification.user_id == user_id,
                                                      Notification.seen_at.is_(None)))
            if row is None:
                return False
            row.body = json.dumps(_json_safe(body))
            if dedupe_key and dedupe_key != row.dedupe_key:
                try:
                    with s.begin_nested():
                        row.dedupe_key = dedupe_key
                except IntegrityError:
                    s.refresh(row)
                    row.body = json.dumps(_json_safe(body))
            return True

    def resolve_notifications(self, user_id: int, kind: str,
                              at: "str | None" = None) -> int:
        """Auto-resolve a user's UNSEEN notifications of ``kind`` by stamping ``seen_at`` — used when
        the condition that raised a state alert no longer holds (the reader opened their waiting
        recommendations, the blind spot closed). The row is kept as history; it simply stops being
        actionable, so the unread badge can never describe a state that has passed. Idempotent;
        returns how many rows were resolved."""
        stamp = at or _utcnow().isoformat()
        with self.session() as s:
            rows = s.scalars(select(Notification).where(
                Notification.user_id == user_id, Notification.kind == kind,
                Notification.seen_at.is_(None))).all()
            for r in rows:
                r.seen_at = stamp
            return len(rows)

    def notification_counts_today(self, user_id: int, kinds: "list[str]", *,
                                  day: "str | None" = None) -> "dict[str, int]":
        """How many notifications of each kind this user already received on ``day`` (``YYYY-MM-DD``,
        UTC, defaulting to today) — the input to a kind's per-day cap.

        Distinct from :meth:`delivered_notification_keys`, which answers "have they seen THIS item".
        A cap answers "have they had ENOUGH items", which only fan-out kinds need: one upstream event
        can mean many notifications, and nothing in the dedupe ledger bounds that.

        Counts the notification's OWN ``created_at`` (the injected evaluation timestamp), not
        ``recorded_at``, so the cap is anchored to the same clock the evaluation used. Matched by
        date prefix — ISO-8601 strings sort lexicographically, the same day-bucketing the reading
        streak uses."""
        if not kinds:
            return {}
        stamp = day or _utcnow().date().isoformat()
        with self.session() as s:
            rows = s.execute(
                select(Notification.kind, func.count())
                .where(Notification.user_id == user_id, Notification.kind.in_(list(kinds)),
                       Notification.created_at >= stamp, Notification.created_at < stamp + "~")
                .group_by(Notification.kind)).all()
        return {kind: int(n) for kind, n in rows}

    # -- notification events (global triggers; see :class:`NotificationEvent`) -------------------
    def record_notification_event(self, source_type: str, source_id: str, *, category: str,
                                  payload: "dict | None" = None, occurred_at: "str | None" = None,
                                  expires_at: "str | None" = None) -> bool:
        """Record a global occurrence, **once**. Returns ``True`` iff this call created it.

        That return value is the whole point: it converts a *level* into an *edge*. The caller asks
        "is this story breaking?" on every ingest cycle and gets ``True`` exactly once, on the first
        cycle that saw it — no separate existence check, no read-then-write race, no state of its own
        to keep. Every later cycle, and every concurrent one, gets ``False``.

        Concurrency-safe by the same argument as :meth:`upsert_user_by_identity`, and deliberately by
        the *same mechanism*: the pre-check is an optimisation for the overwhelmingly common
        "already recorded" case, and ``UNIQUE(source_type, source_id)`` is the arbiter. A caller that
        loses the race sees its ``IntegrityError``, whole transaction rolled back, and reports
        ``False`` — which is exactly right, because the event does now exist and it was not us who
        made it.

        **No SAVEPOINT here, unlike** :meth:`record_notifications` **just above.** That method
        isolates each row of a batch with ``begin_nested()``; this one is a single row and uses a
        second transaction instead. Under the sqlite3 driver's legacy transaction mode a *released*
        savepoint does not participate in the enclosing transaction (measured — see
        ``docs/IDENTITY_UPSERT_CONCURRENCY.md`` §4 and §5), so new code should not lean on it where a
        plain transaction will do."""
        payload = payload if isinstance(payload, dict) else {}
        stamp = occurred_at or _utcnow().isoformat()
        with self.session() as s:
            exists = s.scalar(select(NotificationEvent.id).where(
                NotificationEvent.source_type == source_type,
                NotificationEvent.source_id == source_id))
            if exists is not None:
                return False                    # already recorded -> not an edge
        try:
            with self.session() as s:
                s.add(NotificationEvent(source_type=source_type, source_id=source_id,
                                        category=category, payload=json.dumps(_json_safe(payload)),
                                        occurred_at=stamp, expires_at=expires_at))
                s.flush()                       # surface the conflict here, not at commit
            return True
        except IntegrityError:
            return False                        # a concurrent caller won the edge; it still fired once

    def recent_notification_events(self, *, since: "str | None" = None, now: "str | None" = None,
                                   categories: "list[str] | None" = None,
                                   limit: int = 50) -> "list[dict]":
        """Global events worth showing right now, **newest first**.

        Filters on three things, all of which the caller would otherwise have to re-implement:
        ``since`` (a lower bound on ``occurred_at`` — the reader's horizon), expiry (a row whose
        ``expires_at`` has passed is skipped; ``None`` never expires), and ``categories``. Timestamps
        are ISO-8601 strings compared lexicographically, which is ordering-correct for the UTC
        ``isoformat()`` values this system writes everywhere.

        Returns plain dicts with ``payload`` already decoded, so the delivery boundary can pack them
        straight into a notification context without importing this module's models."""
        moment = now or _utcnow().isoformat()
        with self.session() as s:
            q = select(NotificationEvent)
            if since:
                q = q.where(NotificationEvent.occurred_at > since)
            if categories:
                q = q.where(NotificationEvent.category.in_(list(categories)))
            q = q.where(or_(NotificationEvent.expires_at.is_(None),
                            NotificationEvent.expires_at > moment))
            rows = s.scalars(q.order_by(NotificationEvent.occurred_at.desc(),
                                        NotificationEvent.id.desc()).limit(max(0, limit))).all()
            out = []
            for r in rows:
                try:
                    payload = json.loads(r.payload) if r.payload else {}
                except (ValueError, TypeError):
                    payload = {}                # a corrupt row must not break the whole inbox
                out.append({"id": r.id, "sourceType": r.source_type, "sourceId": r.source_id,
                            "category": r.category, "payload": payload,
                            "occurredAt": r.occurred_at, "expiresAt": r.expires_at})
            return out

    # -- push subscriptions (one row per DEVICE; see :class:`PushSubscription`) ------------------
    #: The category → column map for the denormalised preference mirror. One place, so a new category
    #: is a row here rather than four call sites that can disagree.
    _PUSH_FLAG_COLUMNS = {"breaking": "push_breaking", "digests": "push_digests",
                          "recommendations": "push_recommendations", "product": "push_product"}

    def upsert_push_subscription(self, user_id: int, endpoint: str, *, p256dh: str, auth: str,
                                 content_encoding: str = "aes128gcm",
                                 expires_at: "str | None" = None, user_agent: str = "",
                                 categories: "dict | None" = None,
                                 max_devices: "int | None" = None) -> dict:
        """Register or refresh one device's push subscription. Idempotent on ``endpoint``.

        Three real situations arrive at this one method, and collapsing them is the point:

        * **New device** — insert.
        * **Refresh** — the same browser re-subscribes (its keys rotated, or ``pushsubscriptionchange``
          fired). Same endpoint, possibly new keys: update in place, so the reader does not accumulate
          a row per rotation.
        * **Account switch** — the same browser, now signed in as somebody else. The endpoint moves to
          the new ``user_id``. This is a REASSIGNMENT rather than a second row, because the push
          service will deliver one message to that endpoint and exactly one account may own it.
          Getting this wrong means the previous reader keeps receiving notifications on a device that
          is no longer theirs, which is a privacy failure, not a duplicate-row problem.

        ``categories`` is the reader's per-category push preferences (``{"breaking": {"push": True},
        …}``, the ``settings.notifications.categories`` shape). Mirrored into the indexed columns as a
        query accelerator; unknown categories are ignored, and an absent one means ``False``.

        Returns the stored row plus keys the caller uses for its log line and nothing else:
        ``outcome`` (``created`` / ``updated`` / ``reassigned``), ``previousUserId`` for a
        reassignment, and ``evicted`` — the endpoints dropped by the device cap. They are reported
        from here because this is the only place that can see them without a second query. The API's
        ``response_model`` declares none of them, so all are stripped from the HTTP response.

        **Reassignment requires the subscription's own ``auth`` secret** (P5). Moving an endpoint
        between accounts is legitimate — a shared browser signing in as someone else — but the only
        thing the request proves is that the caller knows the endpoint string, and an endpoint leaks
        far more easily than the secret does: a log, a HAR file, a screenshot. Left unchecked, anyone
        holding one could silently deregister the device that owns it and point future sends at keys
        the real device cannot decrypt.

        ``auth`` is the browser's 16-byte shared secret, minted with the subscription and never
        separable from it — a subscription's endpoint and keys are one unit, and a browser that
        rotates gets a whole new endpoint too. So *same endpoint, different secret* is not a state a
        real browser can be in, and a mismatch is refused (:class:`PushOwnershipError`) rather than
        resolved. Same-user refreshes are not checked: there is no privacy boundary to cross, and the
        strictness would only add a way for a legitimate refresh to fail.

        This is not proof of possession — Web Push offers none without sending a challenge to the
        device, which does not exist yet. It raises the bar from "knows a URL" to "holds the
        subscription material", which is the difference between a leaked log and a compromised
        browser.

        ``max_devices`` bounds how many devices one reader may hold (P7). Over the cap, the
        least-recently-registered are dropped: unbounded rows mean unbounded fan-out cost per
        notification, and the device that has not checked in longest is the one most likely already
        dead. Distinct from the ``410`` pruning Phase B2 will add, which removes endpoints the push
        service has declared gone.

        **Concurrency.** The lookup is an optimisation and ``UNIQUE(endpoint)`` is the arbiter, so two
        callers registering the same *new* endpoint at once both see no row and both insert; the loser
        gets an ``IntegrityError``. That is not rare here — the page's ``subscribe()`` and the service
        worker's ``pushsubscriptionchange`` are exactly the pair that can fire together — and unlike
        :meth:`record_notification_event`, which only reports whether it won, this method must return
        the row either way. So the loser simply retries: the winner's row now exists, the second pass
        takes the update branch, and the caller sees a normal upsert.

        A second transaction rather than a SAVEPOINT, for the reason
        ``docs/IDENTITY_UPSERT_CONCURRENCY.md`` §4 records: under the sqlite3 driver's legacy
        transaction mode a released savepoint does not participate in the enclosing transaction. One
        retry is enough by construction — after an ``IntegrityError`` the row is committed, so the
        second pass cannot take the insert branch. A further failure is a real fault and propagates."""
        flags = self._push_flags(categories)
        for attempt in (0, 1):
            try:
                with self.session() as s:
                    row = s.scalar(select(PushSubscription)
                                   .where(PushSubscription.endpoint == endpoint))
                    if row is None:
                        outcome, previous = "created", None
                        row = PushSubscription(endpoint=endpoint, user_id=user_id)
                        s.add(row)
                    elif row.user_id != user_id:
                        # The privacy boundary. Constant-time because the comparison is against a
                        # secret and the answer is returned to the caller.
                        if not hmac.compare_digest(row.auth or "", auth or ""):
                            raise PushOwnershipError(
                                "This subscription belongs to another account.")
                        outcome, previous = "reassigned", row.user_id
                    else:
                        outcome, previous = "updated", None
                    row.user_id = user_id            # reassignment: the signed-in reader owns it now
                    row.p256dh, row.auth = p256dh, auth
                    row.content_encoding = content_encoding or "aes128gcm"
                    row.expires_at = expires_at
                    row.user_agent = (user_agent or "")[:255]
                    for column, value in flags.items():
                        setattr(row, column, value)
                    row.updated_at = _utcnow()
                    s.flush()                        # surface the conflict here, not at commit
                    if outcome == "created":
                        self._discard_inherited_deliveries(s, row.id)
                    # After the flush, so the row just written is the newest and can never be the one
                    # the cap drops.
                    evicted = self._evict_excess_push_subscriptions(s, user_id, max_devices)
                    return {**self._push_view(row), "outcome": outcome,
                            "previousUserId": previous, "evicted": evicted}
            except IntegrityError:
                if attempt:                          # not the race: a genuine constraint failure
                    raise
        raise AssertionError("unreachable")          # the loop returns or raises on both passes

    @staticmethod
    def _discard_inherited_deliveries(s, subscription_id: int) -> int:
        """Drop delivery-ledger rows left over from a **previous** device that held this row id.

        SQLite reuses rowids. Delete the only subscription and the next insert is handed id 1 again —
        so a reader who is pruned by a `410` and then re-subscribes gets a row that the ledger already
        believes was delivered to, and the fan-out skips them for every notification the *old* device
        received. Silent, and worst exactly when it matters: a `410` prune is ordinary attrition, so
        this is the normal path back, not an exotic one. It happened during the first production
        end-to-end test and showed up only as an unexplained `skipped=2`.

        Called ONLY on the insert branch, and that is what makes it precise rather than destructive: a
        refresh or a reassignment keeps the same row and its history, because the same physical device
        still holds that endpoint. A fresh INSERT is the one case where any pre-existing row carrying
        this id must refer to a device that no longer exists.

        The cost is the pruned device's audit trail, and it is unavoidable rather than chosen: the id
        is how the ledger names a device, so once the id is reused there is no longer anything for
        those rows to mean. Keeping them would preserve bytes at the price of the correctness they
        were supposed to provide.

        **Scoped to the push channel**, which matters now that the ledger is shared. Rowid reuse is a
        fact about `push_subscriptions`; it says nothing about a channel that does not have
        subscriptions at all. The email channel stores a fixed sentinel in this column
        (`email_delivery.ACCOUNT_DESTINATION`, because the address is the account's, not a device's),
        so an unscoped delete here is one id collision away from erasing every email delivery record
        there is — and with it the idempotency that stops a reader being mailed twice."""
        return int(s.execute(delete(NotificationDelivery).where(
            NotificationDelivery.subscription_id == subscription_id,
            NotificationDelivery.channel == "web_push")).rowcount or 0)

    @staticmethod
    def _evict_excess_push_subscriptions(s, user_id: int, max_devices: "int | None") -> list:
        """Drop a reader's least-recently-registered devices past ``max_devices``; returns their
        endpoints. ``None`` or a non-positive cap means unbounded.

        Ordered by ``updated_at`` rather than ``created_at``: a device that re-registers is alive, and
        the one worth losing is the one that has not been heard from, not the one that was set up
        first. Runs inside the caller's transaction, so a cap that cannot be applied cannot leave a
        half-registered device behind."""
        if not max_devices or max_devices <= 0:
            return []
        rows = s.scalars(select(PushSubscription)
                         .where(PushSubscription.user_id == user_id)
                         .order_by(PushSubscription.updated_at.desc(),
                                   PushSubscription.id.desc())).all()
        evicted = []
        for row in rows[max_devices:]:
            evicted.append(row.endpoint)
            s.delete(row)
        return evicted

    @classmethod
    def _push_flags(cls, categories: "dict | None") -> dict:
        """``{"breaking": {"push": True}}`` → ``{"push_breaking": True, …}``, defaulting to False.
        Fail-closed like every other read of a preference: a shape we do not recognise is not consent."""
        cats = categories if isinstance(categories, dict) else {}
        out = {}
        for category, column in cls._PUSH_FLAG_COLUMNS.items():
            entry = cats.get(category)
            out[column] = bool(entry.get("push")) if isinstance(entry, dict) else False
        return out

    @staticmethod
    def _push_view(row: "PushSubscription") -> dict:
        """The wire shape. ``p256dh``/``auth`` are deliberately absent: they are the device's address,
        the sender reads them from the row directly, and nothing outside this module needs them —
        least of all a response that could end up in a log."""
        return {"id": row.id, "endpoint": row.endpoint, "userAgent": row.user_agent,
                "contentEncoding": row.content_encoding, "expiresAt": row.expires_at,
                "categories": {c: getattr(row, col)
                               for c, col in Store._PUSH_FLAG_COLUMNS.items()},
                "createdAt": row.created_at.isoformat() if row.created_at else None,
                "updatedAt": row.updated_at.isoformat() if row.updated_at else None}

    def list_push_subscriptions(self, user_id: int) -> "list[dict]":
        """A reader's registered devices, newest first. Read-only."""
        with self.session() as s:
            rows = s.scalars(select(PushSubscription)
                             .where(PushSubscription.user_id == user_id)
                             .order_by(PushSubscription.id.desc())).all()
            return [self._push_view(r) for r in rows]

    def delete_push_subscription(self, user_id: int, endpoint: str) -> bool:
        """Unregister one device, **user-scoped**: another reader's endpoint is never deleted, even
        when the caller names it exactly. Idempotent — returns whether a row went away."""
        with self.session() as s:
            row = s.scalar(select(PushSubscription).where(
                PushSubscription.endpoint == endpoint, PushSubscription.user_id == user_id))
            if row is None:
                return False
            s.delete(row)
            return True

    def sync_push_subscription_flags(self, user_id: int, categories: "dict | None") -> int:
        """Re-mirror a reader's per-category push preferences onto all of their devices. Called when
        settings change, so the accelerator does not drift from the authority it accelerates. Returns
        how many rows were updated."""
        flags = self._push_flags(categories)
        with self.session() as s:
            rows = s.scalars(select(PushSubscription)
                             .where(PushSubscription.user_id == user_id)).all()
            for row in rows:
                for column, value in flags.items():
                    setattr(row, column, value)
            return len(rows)

    def push_subscriptions_for_category(self, category: str, limit: int = 5000) -> "list[dict]":
        """Every device whose reader has push enabled for ``category`` — the fan-out candidate set.

        This is the query the denormalised ``push_*`` columns exist for: preferences live in an opaque
        JSON blob that cannot be indexed, so without the mirror this would be a full scan plus a JSON
        parse per row. The mirror is an ACCELERATOR and never the authority
        (``docs/BROWSER_PUSH_ARCHITECTURE.md`` §7) — the caller still evaluates
        ``notification_service.gate_path`` against real settings before sending, so a stale flag here
        can only cost a wasted candidate, never an unconsented send.

        Includes the device's keys, because the caller is the sender and they are its address."""
        column = self._PUSH_FLAG_COLUMNS.get(category)
        if column is None:
            return []
        with self.session() as s:
            rows = s.scalars(select(PushSubscription)
                             .where(getattr(PushSubscription, column).is_(True))
                             .order_by(PushSubscription.id.asc()).limit(max(0, limit))).all()
            return [{"id": r.id, "userId": r.user_id, "endpoint": r.endpoint,
                     "p256dh": r.p256dh, "auth": r.auth,
                     "contentEncoding": r.content_encoding} for r in rows]

    def notification_ids_by_dedupe_key(self, user_id: int, keys: "list[str]") -> "dict[str, int]":
        """``dedupe_key -> notification id`` for one reader. The delivery worker records notifications
        idempotently (``record_notifications`` skips ones that already exist, so it cannot report their
        ids) and then needs those ids for the ledger and the payload."""
        if not keys:
            return {}
        with self.session() as s:
            rows = s.execute(select(Notification.dedupe_key, Notification.id)
                             .where(Notification.user_id == user_id,
                                    Notification.dedupe_key.in_(list(keys)))).all()
        return {key: int(nid) for key, nid in rows}

    def claim_delivery(self, notification_id: int, subscription_id: int, *, user_id: int,
                       channel: str = "web_push",
                       now: "datetime | None" = None) -> "int | None":
        """Claim the right to deliver one notification to one destination. Returns the ledger row id,
        or ``None`` if it was already claimed.

        The claim happens BEFORE the send, and that ordering is the whole design. The worker runs on
        every poll cycle over the same still-unexpired notifications; without a claim taken first,
        every cycle would re-send every one of them to every device. ``UNIQUE(notification_id,
        channel, subscription_id)`` is the arbiter, so two workers racing produce one send.

        The claim is a LEASE, not a permanent grant (B3): a retryable failure schedules another
        attempt, and a row abandoned ``pending`` by a process that died mid-send is recoverable via
        :meth:`due_deliveries`. What the UNIQUE constraint still guarantees is that only one such lease
        exists per destination, which is what makes "have we already tried this?" answerable at all.

        ``now`` is the CALLER's clock, not this module's. The worker already has one — every deadline
        in a run is measured against it — and a claim stamped from a second, independent clock is a
        claim the scheduler's own arithmetic disagrees with. They are the same instant in production;
        making the dependency explicit is what lets the ladder be tested in simulated time at all."""
        now = now or _utcnow()
        try:
            with self.session() as s:
                row = NotificationDelivery(notification_id=notification_id, channel=channel,
                                           subscription_id=subscription_id, user_id=user_id,
                                           status="pending", attempts=1,
                                           attempted_at=now, first_attempted_at=now)
                s.add(row)
                s.flush()                    # surface the conflict here, not at commit
                return int(row.id)
        except IntegrityError:
            return None                      # already claimed — by an earlier cycle or a racing worker

    def record_delivery_result(self, delivery_id: int, status: str, *,
                               status_code: "int | None" = None, detail: str = "",
                               next_attempt_at: "datetime | None" = None) -> bool:
        """Resolve a claimed delivery. Returns whether the row was found and updated.

        ``next_attempt_at`` is the entire scheduler. Passing a time leaves the delivery **open** — it
        will be picked up again when that moment arrives; passing ``None`` (the default, and every
        terminal outcome) closes it and stamps ``completed_at``. Nothing else distinguishes "we are
        still trying" from "this is over", which is deliberate: a scheduler whose state can disagree
        with itself is a scheduler that eventually does."""
        with self.session() as s:
            row = s.get(NotificationDelivery, delivery_id)
            if row is None:
                return False
            row.status = status
            row.status_code = status_code
            row.detail = (detail or "")[:255]
            row.next_attempt_at = next_attempt_at
            # An open delivery has no completion time. Stamping one and then reopening the row would
            # make `completed_at` mean "the last time we gave up", which no reader of it expects.
            row.completed_at = None if next_attempt_at is not None else _utcnow()
            return True

    def due_deliveries(self, *, now: "datetime | None" = None, limit: int = 500,
                       lease_seconds: float = 900.0,
                       channel: str = "web_push") -> "list[dict]":
        """Deliveries owed another attempt right now — the retry queue, which is a query and not a queue.

        Two populations, and the second is the one that makes this restart-safe:

        * **scheduled** — ``next_attempt_at`` has arrived. An ordinary backoff coming due.
        * **abandoned** — still ``pending`` and last touched longer than ``lease_seconds`` ago. Nothing
          takes that long: the per-send deadline is measured in seconds. A row in this state means the
          process died between claiming and recording, and without this clause every delivery in flight
          at deploy time would be lost silently — which, on a system that redeploys often, is not an
          edge case but the normal way notifications would go missing.

        Ordered oldest-first so a backlog drains in the order it accumulated rather than starving its
        own head."""
        moment = now or _utcnow()
        stale_before = moment - timedelta(seconds=max(0.0, lease_seconds))
        with self.session() as s:
            q = (select(NotificationDelivery)
                 .where(NotificationDelivery.channel == channel)
                 .where(or_(
                     and_(NotificationDelivery.next_attempt_at.is_not(None),
                          NotificationDelivery.next_attempt_at <= moment),
                     and_(NotificationDelivery.status == "pending",
                          NotificationDelivery.next_attempt_at.is_(None),
                          NotificationDelivery.attempted_at <= stale_before)))
                 .order_by(NotificationDelivery.id.asc()).limit(max(0, limit)))
            return [self._delivery_view(r) for r in s.scalars(q).all()]

    def lease_delivery(self, delivery_id: int, *, attempts: int,
                       now: "datetime | None" = None) -> bool:
        """Take over a due delivery for one more attempt. Returns whether this caller got it.

        ``attempts`` is what :meth:`due_deliveries` reported, and it is checked in the ``WHERE``
        clause — a compare-and-set. Two workers reading the same due row therefore produce one send:
        the first increments the counter, the second's update matches nothing and it backs off. This is
        the same shape as the UNIQUE claim one level up, applied to a row that already exists.

        A single ``UPDATE`` rather than read-modify-write, because the check and the write have to be
        one statement for the compare to mean anything."""
        moment = now or _utcnow()
        with self.session() as s:
            result = s.execute(
                update(NotificationDelivery)
                .where(NotificationDelivery.id == delivery_id,
                       NotificationDelivery.attempts == int(attempts))
                .values(status="pending", attempts=int(attempts) + 1, attempted_at=moment,
                        next_attempt_at=None, completed_at=None))
            return bool(result.rowcount == 1)

    def delivery_attempts(self, notification_id: "int | None" = None, *,
                          user_id: "int | None" = None, limit: int = 200) -> "list[dict]":
        """The ledger, newest first — the operator's answer to "did this reach that device?"."""
        with self.session() as s:
            q = select(NotificationDelivery)
            if notification_id is not None:
                q = q.where(NotificationDelivery.notification_id == notification_id)
            if user_id is not None:
                q = q.where(NotificationDelivery.user_id == user_id)
            rows = s.scalars(q.order_by(NotificationDelivery.id.desc()).limit(max(0, limit))).all()
            return [self._delivery_view(r) for r in rows]

    @staticmethod
    def _delivery_view(r: "NotificationDelivery") -> dict:
        """One ledger row as plain data. Timestamps as ISO strings, except the two the SCHEDULER reads
        back — those stay ``datetime`` because the caller does arithmetic on them, and round-tripping
        through a string to parse it again is where timezone bugs are born."""
        return {"id": r.id, "notificationId": r.notification_id, "channel": r.channel,
                "subscriptionId": r.subscription_id, "userId": r.user_id,
                "status": r.status, "statusCode": r.status_code, "detail": r.detail,
                "attempts": r.attempts,
                "attemptedAt": r.attempted_at.isoformat() if r.attempted_at else None,
                "completedAt": r.completed_at.isoformat() if r.completed_at else None,
                "firstAttemptedAt": r.first_attempted_at,
                "nextAttemptAt": r.next_attempt_at}

    def delivery_backlog(self, *, channel: str = "web_push",
                         now: "datetime | None" = None) -> dict:
        """What is outstanding on the delivery ledger right now — two counts, and they mean different
        things.

        ``pending`` is claimed-but-unresolved: a process died between the send and the record. A
        handful immediately after a deploy is expected and self-healing (the lease recovers them); a
        number that keeps growing means runs are dying rather than one having died.

        ``scheduled`` is the retry ladder's depth — deliveries waiting for their backoff. It rises
        during a push-service outage and falls afterwards, which is the shape to expect; one that
        rises and stays up means the ladder is exhausting rather than succeeding.

        Read at startup and by the runbook, so it is a query rather than something the worker has to
        remember across a restart."""
        moment = now or _utcnow()
        with self.session() as s:
            base = select(func.count()).select_from(NotificationDelivery).where(
                NotificationDelivery.channel == channel)
            return {
                "pending": int(s.scalar(base.where(
                    NotificationDelivery.status == "pending",
                    NotificationDelivery.next_attempt_at.is_(None))) or 0),
                "scheduled": int(s.scalar(base.where(
                    NotificationDelivery.next_attempt_at.is_not(None))) or 0),
                "due": int(s.scalar(base.where(
                    NotificationDelivery.next_attempt_at.is_not(None),
                    NotificationDelivery.next_attempt_at <= moment)) or 0),
            }

    def notification_by_id(self, notification_id: int) -> "dict | None":
        """One notification as the delivery worker needs it — the body a payload is built from.

        The retry path has only ids: the notification it planned from belongs to a run that has since
        ended, possibly in another process. Re-reading rather than caching is what makes a retry
        independent of the run that scheduled it."""
        with self.session() as s:
            row = s.get(Notification, notification_id)
            if row is None:
                return None
            try:
                body = dict(json.loads(row.body))
            except (TypeError, ValueError):
                body = {}                    # a body we cannot read is not a reason to lose the row
        return {**body, "id": row.id}        # id last, so a payload key cannot shadow row identity

    def push_subscription_by_id(self, subscription_id: int) -> "dict | None":
        """One device by id, or ``None`` if it is gone. The retry path must tolerate ``None``: between
        two attempts a reader may have unregistered, or a 410 on another notification may have pruned
        it out from under this one."""
        with self.session() as s:
            row = s.get(PushSubscription, subscription_id)
            if row is None:
                return None
            return {"id": row.id, "userId": row.user_id, "endpoint": row.endpoint,
                    "p256dh": row.p256dh, "auth": row.auth,
                    "contentEncoding": row.content_encoding}

    def delete_push_subscription_by_id(self, subscription_id: int) -> "str | None":
        """Remove a device the push service has declared gone (404/410). Returns its endpoint, so the
        caller can log which one without having kept it. Not user-scoped: the authority here is the
        push service, not a reader."""
        with self.session() as s:
            row = s.get(PushSubscription, subscription_id)
            if row is None:
                return None
            endpoint = row.endpoint
            s.delete(row)
            return endpoint

    def prune_notifications(self, user_id: int, keep: int = 200) -> int:
        """Bound the per-user notification history: delete all but the newest ``keep`` rows. Cadence
        kinds legitimately accumulate one row per period forever, so without this the table grows
        without limit for a long-lived account. Unseen rows are NEVER pruned — only settled history
        is dropped. Returns how many rows were deleted.

        **The delivery ledger goes with them**, and it has to: ``notification_deliveries`` carries a
        real foreign key to ``notifications``, and ``PRAGMA foreign_keys=ON``, so deleting a
        notification that a delivery names raises — on this call, which runs on the delivery boundary
        for every reader on every fetch. B2 introduced that edge without noticing; it only fires once
        a reader passes ``keep`` notifications *and* one of the prunable ones was pushed, which is why
        it was invisible until the retry ladder needed to delete a notification on purpose.

        Dropping the ledger rows is the right resolution rather than the expedient one. The ledger's
        ``subscription_id`` is deliberately not a foreign key, because a record of a send must outlive
        the *address* it was sent to — but the notification is the send's *subject*, and a record that
        we delivered something no longer in existence describes nothing an operator can act on."""
        if keep <= 0:
            return 0
        with self.session() as s:
            keep_ids = [i for (i,) in s.execute(
                select(Notification.id).where(Notification.user_id == user_id)
                .order_by(Notification.id.desc()).limit(keep)).all()]
            if len(keep_ids) < keep:
                return 0
            stale = s.scalars(select(Notification).where(
                Notification.user_id == user_id, Notification.id.notin_(keep_ids),
                Notification.seen_at.is_not(None))).all()
            if not stale:
                return 0
            s.execute(delete(NotificationDelivery).where(
                NotificationDelivery.notification_id.in_([r.id for r in stale])))
            for r in stale:
                s.delete(r)
            return len(stale)

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

    # ---- email channel ------------------------------------------------------------------- #
    def suppress_email(self, address: str, *, reason: str = "bounced", detail: str = "",
                       status_code: "int | None" = None) -> bool:
        """Record that an address must not be written to again. Idempotent — a second bounce for the
        same mailbox is not an error, and must not raise inside a delivery worker."""
        addr = (address or "").strip().lower()
        if not addr:
            return False
        with self.session() as s:
            row = s.get(EmailSuppression, addr)
            if row is not None:
                return False
            s.add(EmailSuppression(address=addr, reason=reason, detail=(detail or "")[:500],
                                   status_code=status_code))
            return True

    def email_suppressed(self, address: str) -> bool:
        addr = (address or "").strip().lower()
        if not addr:
            return False
        with self.session() as s:
            return s.get(EmailSuppression, addr) is not None

    def list_email_suppressions(self, limit: int = 200) -> "list[dict]":
        with self.session() as s:
            rows = s.scalars(select(EmailSuppression)
                             .order_by(EmailSuppression.created_at.desc()).limit(limit)).all()
            return [{"address": r.address, "reason": r.reason, "statusCode": r.status_code,
                     "detail": r.detail,
                     "createdAt": r.created_at.isoformat() if r.created_at else None} for r in rows]

    def notification_job(self, notification_id: int) -> "dict | None":
        """One notification as a sendable job — the same shape :meth:`undelivered_notifications`
        yields, for a RETRY, which starts from a ledger row rather than from a scan.

        Returns ``None`` when the notification is gone (pruned, or the account deleted): a retry
        with nothing to send is resolved rather than looped on forever."""
        with self.session() as s:
            row = s.execute(
                select(Notification, User.email)
                .join(User, User.id == Notification.user_id)
                .where(Notification.id == int(notification_id))).first()
            if row is None:
                return None
            notif, email = row
            try:
                body = json.loads(notif.body)
            except (TypeError, ValueError):
                body = {}
            return {"id": int(notif.id), "userId": int(notif.user_id), "kind": notif.kind,
                    "dedupeKey": notif.dedupe_key, "email": email or "", "body": body,
                    "createdAt": notif.created_at}

    def undelivered_notifications(self, kind: str, *, channel: str, limit: int = 500,
                                  since: "datetime | None" = None) -> "list[dict]":
        """Notifications of ``kind`` with NO delivery row for ``channel`` — the work an email run
        has left to do.

        A LEFT JOIN rather than "list notifications, then ask about each": the digest run touches
        every reader at once, and a per-row existence check is the query that looks fine at fifty
        users and melts at fifty thousand. Ordered oldest-first so a run that hits its deadline has
        spent its time on the mail that has been waiting longest."""
        with self.session() as s:
            q = (select(Notification, User.email)
                 .join(User, User.id == Notification.user_id)
                 .outerjoin(NotificationDelivery,
                            (NotificationDelivery.notification_id == Notification.id)
                            & (NotificationDelivery.channel == channel))
                 .where(Notification.kind == kind, NotificationDelivery.id.is_(None))
                 .order_by(Notification.id.asc()).limit(limit))
            if since is not None:
                q = q.where(Notification.recorded_at >= since)
            out = []
            for notif, email in s.execute(q).all():
                try:
                    body = json.loads(notif.body)
                except (TypeError, ValueError):
                    body = {}
                out.append({"id": int(notif.id), "userId": int(notif.user_id),
                            "kind": notif.kind, "dedupeKey": notif.dedupe_key,
                            "email": email or "", "body": body,
                            "createdAt": notif.created_at})
            return out

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
        # Indexes that failed to create at startup. `index_errors` was written by
        # `_ensure_search_indexes` and read by NOBODY — the shape this repository keeps finding, and
        # the reason it exists at all was a production `SCAN feed_articles` with an index that was
        # supposed to be there and nothing anywhere saying why. Reporting it here is what turns it
        # from a variable into a diagnostic; a missing index degrades silently by definition, so the
        # ONLY signal it can ever give is this one. Empty list = every index is present.
        info["indexErrors"] = [{"index": name, "error": err}
                               for name, err in getattr(self, "index_errors", [])]
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


#: Suffix marking a gzip-compressed backup. Backups are full copies of the database, so a catalog
#: capped at 150,000 articles (~450 MiB) held at the default 12h/7d/4w retention costs ~10 GiB
#: uncompressed. Measured in production: 28 copies of a 93 MB database = 2.4 GB, against a 29 GB
#: volume. SQLite pages of news text compress hard, so this is the cheapest lever there is.
BACKUP_GZ_SUFFIX = ".gz"


def is_compressed_backup(path: str) -> bool:
    return str(path).endswith(BACKUP_GZ_SUFFIX)


def backup_compression() -> bool:
    """Whether new backups are gzipped. ``RWE_BACKUP_COMPRESS=0`` turns it off.

    A kill switch rather than an opt-in, because reading is format-agnostic: every consumer here
    detects the suffix, so turning this off changes only what the NEXT backup is written as and
    leaves every existing ``.db.gz`` restorable."""
    return os.environ.get("RWE_BACKUP_COMPRESS", "1").strip().lower() not in ("0", "false", "no", "off")


@contextmanager
def _as_plain_sqlite(path: str) -> "Iterator[str]":
    """Yield a path to an UNCOMPRESSED SQLite file for ``path``, decompressing to a temp file when
    it is a ``.gz``. Yields the original path untouched otherwise.

    Every reader goes through here, which is what makes compression invisible to callers: a
    ``.db`` written last month and a ``.db.gz`` written today are the same thing to
    ``integrity_ok`` and ``restore_database``. Old backups never stop being restorable."""
    if not is_compressed_backup(path):
        yield path
        return
    tmp_dir = tempfile.mkdtemp(prefix="ih_gunzip_")
    plain = os.path.join(tmp_dir, Path(path).stem)          # strips only the .gz
    try:
        with gzip.open(path, "rb") as fsrc, open(plain, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)
        yield plain
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def integrity_ok(db_path: str) -> bool:
    """True iff ``PRAGMA integrity_check`` reports a single ``ok`` for the SQLite file — the check
    run on a backup before it is trusted, and before a restore replaces the live database.

    Transparently handles a compressed backup by checking the decompressed bytes. A gzip file that
    cannot be decompressed fails the check rather than raising, which is the right answer to
    "is this backup trustworthy": no."""
    try:
        with _as_plain_sqlite(db_path) as plain:
            con = sqlite3.connect(plain)
            try:
                rows = con.execute("PRAGMA integrity_check").fetchall()
                return len(rows) == 1 and rows[0][0] == "ok"
            except sqlite3.DatabaseError:
                return False
            finally:
                con.close()
    except (OSError, EOFError, gzip.BadGzipFile):
        return False


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
    # Decompression happens INSIDE the temp-then-rename, so a compressed restore is exactly as
    # atomic as a plain one: the live path only ever changes by os.replace of a complete file.
    with _as_plain_sqlite(backup_path) as plain:
        shutil.copy2(plain, tmp)
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
    """Backups in ``out_dir`` (``*.db`` and ``*.db.gz``), newest first, with size + mtime.

    Both suffixes, always. A lister that saw only one format would make the other invisible to
    status output, retention and off-host shipping — and an invisible backup is not a backup."""
    p = Path(out_dir)
    if not p.is_dir():
        return []
    out = []
    found = list(p.glob("*.db")) + list(p.glob("*.db" + BACKUP_GZ_SUFFIX))
    for f in sorted(found, key=lambda x: x.stat().st_mtime, reverse=True):
        st = f.stat()
        out.append({"path": str(f), "sizeBytes": st.st_size,
                    "compressed": is_compressed_backup(str(f)),
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
    # ORDER MATTERS: verify the real SQLite file BEFORE compressing it. Checking after would only
    # prove gzip round-tripped whatever it was given, and a faithfully compressed corrupt database
    # is worse than no backup — it looks fine until the restore.
    if not integrity_ok(dest):
        os.remove(dest)
        raise RuntimeError("backup failed its integrity check and was discarded")
    if not backup_compression():
        return dest
    gz = dest + BACKUP_GZ_SUFFIX
    tmp = gz + ".tmp"
    try:
        with open(dest, "rb") as fsrc, gzip.open(tmp, "wb", compresslevel=6) as fdst:
            shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)
        os.replace(tmp, gz)                 # atomic publish, same as the uncompressed path
    except Exception:
        for leftover in (tmp, gz):
            try:
                os.remove(leftover)
            except OSError:
                pass
        return dest                         # compression is an optimisation; keep the good backup
    os.remove(dest)
    return gz
