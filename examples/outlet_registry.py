"""Canonical outlet registry — the product layer's single source of truth for outlet identity.

Three subsystems need to agree on *who an outlet is*: the reading-ingestion scorer (which sees a
URL / domain, e.g. ``https://www.nytimes.com/…``), a reference corpus (which may label the same
outlet ``"New York Times (News)"``), and the onboarding UI (which shows ``"New York Times"``).
Left to their own normalisation these disagree — ``nytimes.com`` normalises to ``"nytimes"`` while
``"New York Times"`` normalises to ``"newyorktimes"`` — so a real read never lines up with the
population's outlet, and captured domains that start with ``w`` were even corrupted by a
``lstrip("www.")`` bug in the research helper.

This module fixes that **in the product layer only**. It loads
:data:`examples/data/outlet_registry.csv` (canonical name · AllSides lean · aliases) and resolves
any of those forms — display name, domain, full URL (incl. subdomains like ``edition.cnn.com``),
or a corpus ``"… (Online News)"`` variant — to one :class:`Outlet` (canonical name + lean).

Nothing here touches the research modules (``rwe/``, ``health_report``, ``simulate_users``,
``narrate_report``); it is a standalone lookup that later milestones will call from the scorer,
the Qbias preprocessor, and onboarding. **Not wired into anything yet.**

    from outlet_registry import default_registry
    reg = default_registry()
    reg.resolve("https://www.washingtonpost.com/2026/politics/x")  # Outlet("Washington Post", -1.0)
    reg.resolve("Fox News (Online News)")                          # Outlet("Fox News", 2.0)
    reg.lean("nytimes.com")                                        # -1.0   (NaN if unknown)
"""

from __future__ import annotations

import csv
import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional
from urllib.parse import urlsplit

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "outlet_registry.csv")
_PARENS = re.compile(r"\([^)]*\)")           # "(Online News)", "(Opinion)", …
_NONALNUM = re.compile(r"[^a-z0-9]+")

#: Legal values for the ``credibility`` column. Blank is legal and means UNCURATED, which is not the
#: same as ``low`` — absence of a verdict never disqualifies an outlet, exactly as absence of a lean
#: never centres one (L2.2).
CREDIBILITY = ("high", "medium", "low")


@dataclass(frozen=True)
class Outlet:
    """A resolved outlet: the canonical display name, its AllSides lean in ``[-2, 2]`` —
    ``NaN`` for a LOCALITY-ONLY row (identity + home are curated facts while the lean stays
    honestly unrated until a defensible public rating is sourced; locality must never require
    guessing a lean — L2.2) — and, Location Intelligence Phase 1, its home locality. Locality
    fields are curated facts (publisher-level, never inferred) and default to ``None`` when
    uncurated, so pre-existing callers and rows are untouched. The dataclass grows by optional
    columns, never by redesign — future publisher metadata (factuality, ownership, transparency)
    follows the same pattern."""
    canonical: str
    lean: float
    country: "str | None" = None    # ISO 3166-1 alpha-2 home country
    region: "str | None" = None     # state / province / ADM1 display name
    city: "str | None" = None
    scope: "str | None" = None      # international | national | regional | local | hyperlocal
    kind: "str | None" = None       # None = a news outlet | "wire" = machine-generated feed
    credibility: "str | None" = None    # high | medium | low — see CREDIBILITY / :func:`is_low_credibility`


def _name_key(text: str) -> str:
    """Comparison key for a *name* form: drop parentheticals, lower-case, drop a leading ``the``,
    strip to alphanumerics. ``"The New York Times"`` and ``"New York Times (News)"`` → ``newyorktimes``."""
    s = _PARENS.sub(" ", str(text)).strip().lower()
    if s.startswith("the "):
        s = s[4:]
    return _NONALNUM.sub("", s)


def _full_key(text: str) -> str:
    """Comparison key that KEEPS a parenthetical. ``"The Star (Malaysia)"`` → ``starmalaysia``.

    :func:`_name_key` drops parentheticals so a corpus label like ``"Fox News (Online News)"``
    reaches Fox News. That is right for a SUFFIX and wrong for a DISAMBIGUATOR: a canonical named
    ``The Star (Malaysia)`` normalises to the bare word ``star``, so it claimed every feed's bare
    "The Star" — including Toronto's, mislabelling a Canadian paper as Malaysian and its lean as
    +2 instead of 0. Measured in production: 4 articles arrived under the bare name.

    A canonical carrying a parenthetical is registered under THIS key only, so it answers to its
    full name and to its explicit aliases, and the generic word is left unclaimed."""
    s = str(text).strip().lower()
    if s.startswith("the "):
        s = s[4:]
    return _NONALNUM.sub("", s)


def _host_of(text: str) -> str:
    """Bare host for a *domain/URL* form: handles an optional scheme, a path with no scheme, and
    strips userinfo / port / a leading ``www.``. ``"https://www.BBC.co.uk/news"`` → ``bbc.co.uk``."""
    s = str(text).strip()
    netloc = urlsplit(s).netloc if "://" in s else s.split("/", 1)[0]
    host = netloc.split("@")[-1].split(":", 1)[0].strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _looks_like_host(text: str) -> bool:
    """Whether ``text`` is a domain/URL rather than a display name. Domains never contain spaces;
    a display name that happens to contain a dot (rare) still routes to the name path."""
    s = str(text).strip()
    if "://" in s:
        return True
    if " " in s:
        return False
    return bool(re.search(r"[a-z0-9-]\.[a-z]{2,}", s.lower()))


def _domain_suffixes(host: str) -> List[str]:
    """Progressively shorter registrable-domain candidates, so a subdomain matches the base:
    ``edition.cnn.com`` → ``["edition.cnn.com", "cnn.com", "com"]``."""
    labels = [l for l in host.split(".") if l]
    return [".".join(labels[i:]) for i in range(len(labels))]


class OutletRegistry:
    """An immutable set of outlets with alias resolution. Build via :meth:`load`."""

    def __init__(self, outlets: List[Outlet], aliases: Dict[str, str]):
        # `outlets` are the distinct canonical outlets; `aliases` maps every lookup key
        # (name keys AND domain keys) to a canonical name.
        self._outlets: Dict[str, Outlet] = {o.canonical: o for o in outlets}
        self._by_name: Dict[str, str] = {}       # name key (parentheticals dropped) -> canonical
        self._by_full: Dict[str, str] = {}       # full key (parentheticals KEPT)    -> canonical
        self._by_domain: Dict[str, str] = {}     # domain key -> canonical
        for key, canonical in aliases.items():
            if _looks_like_host(key):
                self._by_domain[_host_of(key)] = canonical
                continue
            # A name form carrying a parenthetical is registered ONLY under its full key. For a
            # canonical that is the whole point — `The Star (Malaysia)` must not claim the bare word
            # `star` and mislabel Toronto's paper. For an ALIAS it costs nothing, since an alias
            # with a parenthetical is written to match one specific form.
            self._by_full[_full_key(key)] = canonical
            if "(" not in key:
                self._by_name[_name_key(key)] = canonical

    # -- construction ----------------------------------------------------- #
    @classmethod
    def load(cls, path: "str | None" = None) -> "OutletRegistry":
        """Load the registry CSV (defaults to the bundled ``data/outlet_registry.csv``).

        ``#`` lines and blanks are skipped. Each row is ``canonical, lean, aliases`` plus the
        optional Phase-1 locality columns ``country, region, city, scope``, then ``kind`` and
        ``credibility`` (missing / blank → ``None`` — a two-column legacy file still loads
        unchanged, and so does every row written before a column existed).
        ``aliases`` is ``|``-separated. The canonical name is itself registered as a lookup key."""
        path = path or _DATA
        outlets: List[Outlet] = []
        aliases: Dict[str, str] = {}

        def _opt(row: list, i: int) -> "str | None":
            v = row[i].strip() if len(row) > i and row[i] and row[i].strip() else None
            return v

        with open(path, encoding="utf-8") as f:
            reader = csv.reader(l for l in f if l.strip() and not l.lstrip().startswith("#"))
            header = next(reader, None)            # skip the column header
            for row in reader:
                if len(row) < 2 or not row[0].strip():
                    continue
                canonical = row[0].strip()
                # Blank lean = a deliberate locality-only row (unrated -> NaN, the same "unknown"
                # convention the scorer already speaks). Garbage still raises — fail loudly.
                raw_lean = row[1].strip()
                lean = float(raw_lean) if raw_lean else float("nan")
                cred = _opt(row, 8)
                outlets.append(Outlet(canonical=canonical, lean=lean,
                                      country=_opt(row, 3), region=_opt(row, 4),
                                      city=_opt(row, 5), scope=_opt(row, 6),
                                      kind=_opt(row, 7),
                                      credibility=cred.lower() if cred else None))
                aliases[canonical] = canonical      # the name itself resolves
                if len(row) >= 3 and row[2].strip():
                    for alias in row[2].split("|"):
                        alias = alias.strip()
                        if alias:
                            aliases[alias] = canonical
        return cls(outlets, aliases)

    # -- resolution ------------------------------------------------------- #
    def resolve(self, text: "str | None") -> Optional[Outlet]:
        """Resolve any form (name / domain / URL / corpus variant) to an :class:`Outlet`, or
        ``None`` if unknown. Domain forms match by registrable-domain suffix (subdomain-tolerant)."""
        if not text:
            return None
        if _looks_like_host(text):
            host = _host_of(text)
            for cand in _domain_suffixes(host):
                canonical = self._by_domain.get(cand)
                if canonical:
                    return self._outlets[canonical]
            # a bare word that looked host-ish but isn't a known domain: fall through to name
        # Full key first: it is the exact form, so `The Star (Malaysia)` and a corpus variant of it
        # both land. Then the parenthetical-stripped key, which is what makes
        # `Fox News (Online News)` reach Fox News — but which only DISAMBIGUATED canonicals decline
        # to register, so a bare `The Star` finds nothing rather than the wrong newspaper.
        canonical = self._by_full.get(_full_key(text)) or self._by_name.get(_name_key(text))
        return self._outlets.get(canonical) if canonical else None

    def canonical(self, text: "str | None") -> Optional[str]:
        """The canonical outlet name for ``text``, or ``None``."""
        o = self.resolve(text)
        return o.canonical if o else None

    def lean(self, text: "str | None") -> float:
        """The AllSides lean for ``text``, or ``NaN`` if unknown OR the row is locality-only
        (unrated) — either way the engine excludes it from lean-based metrics, never defaults."""
        o = self.resolve(text)
        return o.lean if o else float("nan")

    def is_wire(self, text: "str | None") -> bool:
        """Whether ``text`` resolves to a machine-generated feed rather than a news outlet.

        A ``wire`` row means the source publishes auto-generated market-data and press-release
        copy — "X LLC Makes New Investment in Y Inc", "Z Posts Quarterly Earnings Results". Such
        copy clusters *correctly* (a template repeated 115 times really is about one template), so
        no clustering signal can find it: measured, ``geoCoherence`` rates it perfectly coherent
        and articles-per-publisher was tested against the whole catalog and rejected at 0%
        precision and 0% recall (docs/PUBLISHER_CONCENTRATION_EVALUATION.md).

        It is an identity fact about the SOURCE, so it belongs here — explicit, auditable, one
        cell to reverse, and incapable of misfiring on a government-funding story the way a
        threshold in the clustering path would. Unknown outlets are never wire: absence of a row
        means unrated, not disqualified."""
        o = self.resolve(text)
        return bool(o and o.kind == "wire")

    def credibility(self, text: "str | None") -> Optional[str]:
        """The curated credibility verdict for ``text`` — ``high`` / ``medium`` / ``low``, or
        ``None`` when the outlet is unknown OR the column is uncurated."""
        o = self.resolve(text)
        return o.credibility if o else None

    def is_low_credibility(self, text: "str | None") -> bool:
        """Whether ``text`` resolves to an outlet whose LEAN should not be voted at full weight.

        This column exists because a lean and a credibility verdict are different facts, and the
        file could previously express only one of them. Eight outlets — Xinhua, Global Times, RT,
        Sputnik, TASS, The Economic Times, Daily Star (UK), GB News — have a published MBFC lean and
        an MBFC ``Questionable`` / ``Low Credibility`` verdict beside it. With one column the only
        honest option was to withhold the lean entirely, which lost a true fact to avoid a
        misleading one. With two, the lean is recorded AND the caveat travels with it.

        The bar is the RATER's own verdict, never an impression of the outlet: state-aligned outlets
        MBFC rates at Medium or better are rated and voted normally here (Daily Sabah, Ahram Online,
        Anadolu Agency). Without that constraint the column would drift into "outlets I distrust",
        which is the fabrication this file exists to prevent, pointed the other way.

        Unknown and uncurated outlets are never low: absence of a verdict is not a verdict."""
        return self.credibility(text) == "low"

    def outlets(self) -> List[Outlet]:
        """All distinct outlets: rated ones ordered by lean then name, locality-only (NaN lean)
        rows deterministically last by name (NaN sort keys would otherwise be order-unstable)."""
        return sorted(self._outlets.values(),
                      key=lambda o: (math.isnan(o.lean),
                                     0.0 if math.isnan(o.lean) else o.lean, o.canonical))

    def __len__(self) -> int:
        return len(self._outlets)

    def __contains__(self, text) -> bool:
        return self.resolve(text) is not None


_DEFAULT: "OutletRegistry | None" = None


def default_registry() -> OutletRegistry:
    """The process-wide registry loaded from the bundled data (built once, then cached)."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = OutletRegistry.load()
    return _DEFAULT


def resolve(text: "str | None") -> Optional[Outlet]:
    """Convenience: resolve against the default registry."""
    return default_registry().resolve(text)


def is_wire(text: "str | None") -> bool:
    """Convenience: :meth:`OutletRegistry.is_wire` against the default registry."""
    return default_registry().is_wire(text)


def credibility(text: "str | None") -> Optional[str]:
    """Convenience: :meth:`OutletRegistry.credibility` against the default registry."""
    return default_registry().credibility(text)


def is_low_credibility(text: "str | None") -> bool:
    """Convenience: :meth:`OutletRegistry.is_low_credibility` against the default registry."""
    return default_registry().is_low_credibility(text)


def lint_registry(path: "str | None" = None) -> List[dict]:
    """Read-only well-formedness check on the registry CSV. Returns a list of issue dicts
    ``{severity, code, line, message}`` (empty ⇒ clean); NEVER raises on a malformed file (that is
    the point) and never mutates anything. Mirrors :meth:`OutletRegistry.load`'s parsing (``#`` and
    blank lines skipped, the first content line is the column header) and checks:

      * ``malformed_row``      — fewer than two columns, or a blank canonical
      * ``invalid_lean``       — column 2 is not a finite number in ``[-2, 2]``
      * ``duplicate_canonical``— the same canonical name defined on two rows
      * ``duplicate_alias``    — one alias key mapped to two different canonicals (resolution would
                                 depend on row order — a real bug)
      * ``repeated_alias_in_row`` (warning) — the same alias listed twice in one row's alias list
      * ``invalid_credibility``— column 9 is neither blank nor one of :data:`CREDIBILITY`
      * ``unrated_low_credibility`` (warning) — a ``low`` row with no lean. Legal, but it means the
        row is asserting a caveat about a rating that is not there, which is almost always a
        half-finished edit: the point of ``low`` is to let the LEAN be recorded.
    """
    path = path or _DATA
    issues: List[dict] = []
    seen_canonical: Dict[str, int] = {}     # canonical -> first line number
    alias_owner: Dict[str, str] = {}        # normalized alias key -> canonical
    with open(path, encoding="utf-8") as f:
        lines = list(enumerate(f, 1))
    header_skipped = False
    for lineno, raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not header_skipped:              # the loader consumes the first content line as the header
            header_skipped = True
            continue
        cells = next(csv.reader([raw]), [])
        if len(cells) < 2 or not cells[0].strip():
            issues.append({"severity": "error", "code": "malformed_row", "line": lineno,
                           "message": f"line {lineno}: expected 'canonical,lean,aliases', got {raw.strip()!r}"})
            continue
        canonical = cells[0].strip()
        raw_lean = cells[1].strip()
        # A BLANK lean is legal: a locality-only row (unrated). A non-blank lean must be a
        # finite number in [-2, 2] — "NaN"/garbage is a data error, not a way to say unrated.
        if raw_lean:
            try:
                lean = float(raw_lean)
                if not math.isfinite(lean) or not (-2.0 <= lean <= 2.0):
                    raise ValueError
            except ValueError:
                issues.append({"severity": "error", "code": "invalid_lean", "line": lineno,
                               "message": f"line {lineno} ({canonical}): lean {raw_lean!r} "
                                          "is not a finite number in [-2, 2] (blank = unrated)"})
        cred = cells[8].strip().lower() if len(cells) > 8 else ""
        if cred and cred not in CREDIBILITY:
            issues.append({"severity": "error", "code": "invalid_credibility", "line": lineno,
                           "message": f"line {lineno} ({canonical}): credibility {cred!r} is not one "
                                      f"of {'/'.join(CREDIBILITY)} (blank = uncurated)"})
        elif cred == "low" and not raw_lean:
            issues.append({"severity": "warning", "code": "unrated_low_credibility", "line": lineno,
                           "message": f"line {lineno} ({canonical}): credibility 'low' with no lean — "
                                      "the point of 'low' is to let the lean be recorded WITH the caveat"})
        if canonical in seen_canonical:
            issues.append({"severity": "error", "code": "duplicate_canonical", "line": lineno,
                           "message": f"line {lineno}: canonical {canonical!r} already defined at "
                                      f"line {seen_canonical[canonical]}"})
        else:
            seen_canonical[canonical] = lineno
        alias_cell = cells[2] if len(cells) >= 3 else ""
        local: set = set()
        for alias in alias_cell.split("|"):
            alias = alias.strip()
            if not alias:
                continue
            if alias in local:
                issues.append({"severity": "warning", "code": "repeated_alias_in_row", "line": lineno,
                               "message": f"line {lineno} ({canonical}): alias {alias!r} repeated in the row"})
            local.add(alias)
            # Mirrors the loader's registration rule exactly (see OutletRegistry.__init__): a form
            # with a parenthetical is keyed on its FULL text, so two disambiguated names that share
            # a bare word are not reported as colliding when resolution keeps them apart.
            key = (_host_of(alias) if _looks_like_host(alias)
                   else _full_key(alias) if "(" in alias else _name_key(alias))
            if key in alias_owner and alias_owner[key] != canonical:
                issues.append({"severity": "error", "code": "duplicate_alias", "line": lineno,
                               "message": f"line {lineno}: alias {alias!r} maps to {canonical!r} but "
                                          f"already maps to {alias_owner[key]!r}"})
            else:
                alias_owner[key] = canonical
    return issues
