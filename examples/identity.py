"""identity.py — durable ids for the two things every commercial product joins on.

**Articles.** ``feed_articles`` is keyed by canonical URL, and the URL string is the foreign key
everywhere (reads, saves, the story ledger). A URL is a 2 KB mutable string and canonicalisation
is a rule that has changed before, so a customer cannot store it as a reference. ``article_id``
(``ar_`` + 20 hex, :func:`store.article_id_for`) is minted ONCE on first sight and every URL form
ever observed resolves to it through ``article_aliases``. The consumer path keeps using canonical
URLs and notices nothing.

**Publishers.** Identity today is a name string resolved at read time. ``publisher_id`` (``pub_``
+ 20 hex) is a pure function of the outlet's IDENTITY KEY — the same token
``publisher_identity.groups`` assigns a name: the registry canonical when the name resolves, the
brand domain for a host form, the folded name key otherwise — so ingest stamps it with no lookup,
two processes agree without coordination, and every curated alias collapses by construction. The
``publishers`` table is a materialised view of the registry + the catalogue (:func:`sync_publishers`)
that the platform reads by id; the registry CSV stays the source of truth for every curated fact.

**Versions.** :func:`registry_version` names the registry snapshot a fact was read under, so a
served publisher fact can say which curation it reflects.

Design: docs/NEWS_INTELLIGENCE_INFRASTRUCTURE.md §D.4 / §E.1 / §E.3.
"""

from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime, timezone
from typing import Optional

import outlet_registry
import publisher_identity
import store as _store

article_id_for = _store.article_id_for

#: Bump when :func:`publisher_identity_key` would map an existing name to a different key.
PUBLISHER_ID_SCHEME = "1"


def publisher_identity_key(name: "str | None") -> str:
    """The identity token for ONE publisher name — the per-name half of ``publisher_identity.groups``.

    ``c:<canonical>`` when the registry resolves the name (every curated alias collapses here),
    ``d:<brand domain>`` for a host form (``kfbk.iheart.com`` -> ``d:iheart.com``), ``n:<name key>``
    otherwise. The label bridge ``groups`` applies across a whole name SET (a bare ``Sportskeeda``
    joining ``sportskeeda.com``) needs the set and is deliberately not applied per name: an id must
    be a function of the name alone, or two processes would disagree."""
    text = str(name or "").strip()
    if not text:
        return ""
    resolved = outlet_registry.resolve(text)
    if resolved is not None:
        return "c:" + resolved.canonical
    if outlet_registry._looks_like_host(text):
        host = outlet_registry._host_of(text)
        return "d:" + (publisher_identity._brand_domain(host) or host)
    return "n:" + (outlet_registry._name_key(text) or text.lower())


def publisher_id_for_key(key: str) -> Optional[str]:
    if not key:
        return None
    return "pub_" + hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:20]


def publisher_id_for(name: "str | None") -> Optional[str]:
    """The durable publisher id for a name in any form the feeds use, or ``None`` for a blank."""
    return publisher_id_for_key(publisher_identity_key(name))


_REGISTRY_VERSION_CACHE: dict = {}


def registry_version(path: "str | None" = None) -> str:
    """``sha256:<16 hex>`` of the registry file's bytes — the curation snapshot id. Cached by
    (path, mtime, size), so it is a stat per call and a read per change."""
    p = path or outlet_registry._DATA
    try:
        st = os.stat(p)
    except OSError:
        return "unknown"
    key = (p, st.st_mtime_ns, st.st_size)
    v = _REGISTRY_VERSION_CACHE.get(key)
    if v is None:
        with open(p, "rb") as f:
            v = "sha256:" + hashlib.sha256(f.read()).hexdigest()[:16]
        _REGISTRY_VERSION_CACHE.clear()
        _REGISTRY_VERSION_CACHE[key] = v
    return v


def registry_facts(o: "outlet_registry.Outlet") -> dict:
    """The curated columns as the ``publishers`` row carries them. Lean travels with its
    provenance (``allsides``) and only when finite — a locality-only row stays unrated."""
    finite = isinstance(o.lean, float) and math.isfinite(o.lean)
    return {"lean": float(o.lean) if finite else None,
            "lean_source": "allsides" if finite else None,
            "country": o.country, "region": o.region, "city": o.city, "scope": o.scope,
            "kind": o.kind, "credibility": o.credibility,
            "factuality": o.factuality, "factuality_source": o.factuality_source,
            "factuality_asof": o.factuality_asof,
            "ownership": o.ownership, "ownership_source": o.ownership_source,
            "ownership_asof": o.ownership_asof, "ownership_owner": o.ownership_owner}


def sync_publishers(store_, *, registry=None, catalogue: bool = True) -> dict:
    """Materialise ``publishers`` + ``publisher_hosts`` from the registry, then refresh every
    row's catalogue counts. Idempotent; a few hundred milliseconds on a 600-row registry."""
    reg = registry or outlet_registry.default_registry()
    version = registry_version()
    created = updated = hosts = 0
    try:
        import corpus
        tier_of = corpus.tier_of
    except Exception:                      # tiering is optional for identity
        tier_of = None
    for o in reg.outlets():
        key = "c:" + o.canonical
        pid = publisher_id_for_key(key)
        domains = reg.domains(o.canonical)
        tier = None
        if tier_of is not None:
            try:
                tier = tier_of(o.canonical)
            except Exception:
                tier = None
        was_created = store_.upsert_publisher(publisher_id=pid, identity_key=key,
                                              name=o.canonical, registered=True,
                                              facts=registry_facts(o), registry_version=version,
                                              tier=tier, hosts=domains)
        created += int(was_created)
        updated += int(not was_created)
        hosts += len(domains)
    counted = 0
    if catalogue:
        for pid, (n, first, last) in store_.publisher_article_counts().items():
            if store_.upsert_publisher(publisher_id=pid, identity_key="", name="", articles=n,
                                       first_seen=first, last_seen=last, create=False) is None:
                continue
            counted += 1
    return {"registryVersion": version, "created": created, "updated": updated,
            "hosts": hosts, "counted": counted,
            "syncedAt": datetime.now(timezone.utc).isoformat()}


__all__ = ["PUBLISHER_ID_SCHEME", "article_id_for", "publisher_identity_key",
           "publisher_id_for", "publisher_id_for_key", "registry_version", "registry_facts",
           "sync_publishers"]
