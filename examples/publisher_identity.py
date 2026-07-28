"""publisher_identity.py — one outlet, one identity, whatever form the feed used.

Feeds do not agree on how to name an outlet. The same masthead arrives as ``Sportskeeda.Com`` and
``Sportskeeda``; a syndication network arrives as a hundred station hostnames under ``iheart.com``.
``publisherCount`` counts distinct STRINGS, so every such form inflates it — and that count is
load-bearing in four places:

* ``min_publishers = 2`` decides whether a cluster becomes a story at all, so **one outlet under
  several names can be admitted as if several outlets had covered it**. Measured on the live
  catalog: 35 stories, the largest being 17 articles from 17 iHeart station hostnames;
* the default ranking sorts on it (``Samsung Galaxy Z Fold 8`` sat near the top on 26 publishers
  that are really 8);
* ``_distribution`` gives one lean vote per outlet, so a fragmented outlet votes many times;
* the ``>= 3 rated publishers`` floor for a coverage-gap claim.

Extracted from ``audit_publisher_identity`` so the audit and the pipeline share one definition —
an audit that measured a different rule than production applies would be measuring nothing.
"""

from __future__ import annotations

import outlet_registry
import publisher_wiki


def _brand_domain(host: str) -> str:
    """The true registrable domain: brand label plus its public suffix.

    ``publisher_wiki.registrable_domain`` keeps subdomains, so a hundred iHeart stations look like
    a hundred domains. The bare brand LABEL is the opposite error and a worse one — it collapses
    ``standard.net.au`` (the Warrnambool Standard) into ``standard.co.uk`` (the London Evening
    Standard), which are unrelated newspapers. Label + suffix keeps ``kfbk.iheart.com`` with
    ``wjjs.iheart.com`` and keeps those two Standards apart."""
    label = publisher_wiki.domain_label(host)
    parts = [p for p in str(host or "").strip().lower().split(".") if p]
    if not label or label not in parts:
        return ".".join(parts)
    for i in range(len(parts) - 1, -1, -1):     # last occurrence: brand may repeat in a subdomain
        if parts[i] == label:
            return ".".join(parts[i:])
    return ".".join(parts)


def ambiguous_labels(names) -> set:
    """Brand labels carried by more than one domain in this name set.

    A bare name whose label is in here cannot be placed: ``Standard`` could be standard.net.au or
    standard.co.uk, and guessing would merge two newspapers. Exposed because the audit needs to
    report exactly this, and reconstructing it from the group map is not possible — ``groups``
    roots each set at its lexicographic minimum, which is usually the NAME rather than the token,
    so "did this name join a domain" cannot be read off the key."""
    label_domains: dict = {}
    for name in names:
        resolved = outlet_registry.resolve(name)
        base = resolved.canonical if resolved else (name or "")
        if outlet_registry._looks_like_host(base):
            dom = _brand_domain(base)
            label_domains.setdefault(publisher_wiki.domain_label(base) or dom, set()).add(dom)
    return {label for label, domains in label_domains.items() if len(domains) > 1}


def groups(names) -> dict:
    """``publisher name -> identity key``, collapsing the forms that are one outlet.

    Registry first: a name that resolves is keyed by its CANONICAL, so every curated alias collapses
    by construction and ``BBC News`` meets ``bbc.co.uk``. Beyond that, two rules with different
    strengths, because the evidence differs:

    * **Two host forms** collapse only on a matching brand domain. Same masthead, same domain.
    * **A host and a bare name** collapse on the brand label — that is the only way ``Sportskeeda``
      reaches ``Sportskeeda.Com`` when neither is curated. Applied ONLY when exactly one domain
      carries that label: a bare ``Standard`` is genuinely ambiguous between two newspapers, and
      guessing would merge them.
    """
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    label_domains: dict = {}
    tokens: dict = {}
    for name in names:
        resolved = outlet_registry.resolve(name)
        base = resolved.canonical if resolved else (name or "")
        if outlet_registry._looks_like_host(base):
            dom = _brand_domain(base)
            tokens[name] = "d:" + dom
            label_domains.setdefault(publisher_wiki.domain_label(base) or dom, set()).add(dom)
        else:
            tokens[name] = "n:" + (outlet_registry._name_key(base) or base.strip().lower())
        union(name, tokens[name])

    for label, domains in label_domains.items():
        if len(domains) == 1:                   # unambiguous: a bare name may join it
            union("n:" + label, "d:" + next(iter(domains)))

    return {name: find(name) for name in names}


