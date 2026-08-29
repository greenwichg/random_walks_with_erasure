"""source_directory.py — the DIRECTORY acquisition channel. Pure: no store, no network, no writes.

**Phase 2 of `docs/SCALE_ROADMAP.md`, the channel that needs nothing switched on.** It turns a
structured list of outlets — a national media register, a press-council membership roll, a
per-country index, an open media dataset — into `source_discovery` evidence records.

## Why this channel exists, and why it is first

The catalogue channel (`source_discovery.catalogue_evidence`) **cannot add an outlet**: it mines
hosts we already ingest, so admitting one reclassifies rather than acquires. Every net-new outlet
has to come from somewhere else, and of the somewhere-elses this is the cheapest by a wide margin:

* **Nearly request-free.** One file, hundreds of hosts. Compare the web channel, which spends a
  search request per query and then the probe's three per host.
* **It brings the evidence the other channels lack.** A register entry states a country and usually
  a language. That is exactly what `source_validation`'s gate 6 reports ``UNKNOWN`` for on a
  catalogue candidate, and exactly the axis M14 wanted to target and could not.
* **No ToS question of its own.** Reading a published list is not crawling a newsroom. The probe
  that follows still asks robots.txt, as it does for every channel.

It does not reach 45,000 either — no channel does, which is why Phase 2 is a portfolio — but it is
the one that can run today.

## What this module deliberately does NOT do

**It does not fetch.** ``parse`` takes text. An operator obtains the register however their policy
says to, and the module is a parser — the same structural choice `source_validation` makes about its
own fetcher, for the same reason: a module with no way to reach the network cannot quietly reach it.

**It does not decide.** It emits evidence records; `source_discovery.gate` applies the shared gates
(already tracked, aggregator/proxy) and stamps eligibility. A directory entry is a *claim* that
something is a news outlet, and the probe is what settles it.

    python examples/source_directory.py --file registers/za.csv          # parse and report
    python examples/source_directory.py --file registers/za.csv --json   # machine-readable
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import outlet_registry

#: Column names accepted for each field, lower-cased. Several spellings because these files come
#: from registers written by different bodies in different decades, and renaming a column by hand
#: before every import is how a channel acquires a manual step it never loses.
_FIELDS = {
    "host": ("host", "domain", "website", "url", "site", "link", "homepage"),
    "name": ("name", "outlet", "publisher", "title", "publication"),
    "country": ("country", "country_code", "countrycode", "cc", "nation"),
    "language": ("language", "lang", "language_code"),
}

#: A country code we will carry. Two letters, and nothing else — `store.FeedArticle.country` is
#: ``VARCHAR(2)``, so a register's "United Kingdom" is dropped rather than truncated to "Un".
_CC_LEN = 2


def _pick(header: "list[str]") -> dict:
    """Map our field names onto this file's column indexes. Absent fields are simply absent."""
    lower = [(h or "").strip().lower() for h in header]
    out = {}
    for field, names in _FIELDS.items():
        for i, h in enumerate(lower):
            if h in names:
                out[field] = i
                break
    return out


def _norm_cc(value: str) -> str:
    """A two-letter upper-case country code, or ``""``.

    Long-form country names are DROPPED rather than guessed at. A register that writes "Deutschland"
    is telling us something we cannot map without a table nobody has curated, and a wrong country on
    a source is worse than an absent one: `audit_story_geography` and the Country filter both read
    it, so a bad value becomes a visible product claim rather than a silent gap."""
    v = (value or "").strip()
    return v.upper() if len(v) == _CC_LEN and v.isalpha() else ""


def _norm_lang(value: str) -> str:
    """A lower-cased language tag, primary subtag only (``pt-BR`` -> ``pt``).

    Primary subtag because that is what `rss_ingest.parse_feed` fills ``language`` with and what
    `source_discovery`'s evidence carries; keeping the region here would make a directory-supplied
    language incomparable with an ingested one."""
    v = (value or "").strip().lower().replace("_", "-")
    return v.split("-", 1)[0] if v and v.replace("-", "").isalpha() else ""


def parse(text: str, *, source_ref: str = "") -> "list[dict]":
    """A register's text -> `source_discovery` evidence records, deduplicated by canonical host.

    Accepts a delimited table (comma, tab or semicolon) with a recognised header, or a bare list of
    one domain/URL per line. The bare-list form matters: half of what is publicly available is a
    Wikipedia section or a regulator's PDF pasted into a text file, and requiring a header would
    mean hand-editing every one.

    Every host goes through `outlet_registry._host_of`, which strips scheme, userinfo, port,
    ``www.``, case and a trailing dot — so ``https://WWW.Example.CO.ZA/news`` and ``example.co.za``
    are one record, not two. Within-file duplicates collapse; cross-file and cross-channel
    duplicates collapse at the store, because `SourceAdmission.host` is the primary key.

    ``articles`` is 0 on every record and that is not a gap: this channel has no volume evidence and
    never will, which is why it supplies `source_discovery.always_admissible` rather than a floor.
    """
    rows = _rows(text)
    out: dict = {}
    for rec in rows:
        raw = (rec.get("host") or "").strip()
        if not raw or not outlet_registry._looks_like_host(raw):
            continue
        host = outlet_registry._host_of(raw)
        if not host or "." not in host:
            continue
        name = (rec.get("name") or "").strip()
        cc = _norm_cc(rec.get("country") or "")
        lang = _norm_lang(rec.get("language") or "")
        prev = out.get(host)
        if prev is None:
            out[host] = {
                "host": host,
                "articles": 0,
                "publishers": [name] if name else [],
                "language": lang,
                "country": cc,
                # What the probe will be pointed at, and what an auditor can check the claim
                # against. `sampleUrls` is the key `source_validation` and the campaign already
                # print, so the directory's homepage lands where a reviewer already looks.
                "sampleUrls": [f"https://{host}/"],
                "directoryRef": source_ref,
            }
            continue
        # A register listing an outlet twice (two regions, two formats) is ordinary. Keep the first
        # of everything and fill only what was missing, so a later row with a blank country cannot
        # erase an earlier row's.
        if name and name not in prev["publishers"]:
            prev["publishers"].append(name)
        prev["language"] = prev["language"] or lang
        prev["country"] = prev["country"] or cc
    return list(out.values())


def _rows(text: str) -> "list[dict]":
    """Delimited-with-header rows, or one-host-per-line rows. Never raises on a malformed file."""
    body = (text or "").strip()
    if not body:
        return []
    try:
        dialect = csv.Sniffer().sniff(body[:4096], delimiters=",\t;")
        reader = list(csv.reader(io.StringIO(body), dialect))
    except Exception:
        reader = []
    if reader and len(reader[0]) > 1:
        cols = _pick(reader[0])
        if "host" in cols:
            out = []
            for row in reader[1:]:
                rec = {f: (row[i].strip() if i < len(row) else "") for f, i in cols.items()}
                out.append(rec)
            return out
    # Bare list. A leading "#" is a comment, and a line with whitespace is prose rather than a host
    # — a pasted register is full of both.
    return [{"host": line.strip()} for line in body.splitlines()
            if line.strip() and not line.strip().startswith("#")]


def census(records: list) -> dict:
    """What a parse produced, by the evidence it carries. Printed rather than assumed, because a
    register whose country column we failed to recognise still yields hosts and would otherwise look
    like a successful import of exactly the field this channel exists to bring."""
    return {
        "hosts": len(records),
        "withCountry": sum(1 for r in records if r.get("country")),
        "withLanguage": sum(1 for r in records if r.get("language")),
        "withName": sum(1 for r in records if r.get("publishers")),
        "countries": len({r["country"] for r in records if r.get("country")}),
        "languages": len({r["language"] for r in records if r.get("language")}),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True, help="a register: delimited with a header, or one host per line")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    with open(args.file, encoding="utf-8", errors="replace") as f:
        records = parse(f.read(), source_ref=os.path.basename(args.file))
    stats = census(records)
    if args.json:
        print(json.dumps({"census": stats, "records": records}, indent=2, sort_keys=True))
        return 0

    print(f"=== {args.file} ===")
    print(f"  hosts (deduplicated) : {stats['hosts']:,}")
    print(f"  with a country       : {stats['withCountry']:,}  across {stats['countries']} countries")
    print(f"  with a language      : {stats['withLanguage']:,}  across {stats['languages']} languages")
    print(f"  with a name          : {stats['withName']:,}")
    if stats["hosts"] and not stats["withCountry"]:
        print("\n  NOTE: no country was recognised on any row. This channel's whole advantage is")
        print("        bringing country and language with it — check the header names against")
        print("        source_directory._FIELDS before importing.")
    print("\n  Nothing has been written. Seed with:")
    print(f"    python examples/source_campaign.py seed --channel directory --file {args.file} --db ...")
    for r in records[:10]:
        print(f"    {r['host']:<38} {r.get('country') or '--':<3} {r.get('language') or '--':<3} "
              f"{(r['publishers'][0] if r['publishers'] else '')[:28]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
