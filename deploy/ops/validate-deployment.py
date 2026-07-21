#!/usr/bin/env python3
"""validate-deployment.py — deployment-dependency guard. DEPLOYMENT-ONLY (CI + local; no app change).

Renders the *merged* Docker Compose model (base + AWS override) and asserts that every service which
DECLARES a capability also carries the config that capability DEPENDS ON. It exists to stop one class
of production drift: a feature is turned on but its wiring is missing, so the stack looks healthy while
silently doing nothing.

Concretely it catches things like the 2026-07-21 incident — `RWE_FEED_POLL=1` on the `api` service with
no `RWE_RSS_FEEDS` and no feed-file mount, so the background poller resolved zero feeds and ingested
nothing. And it generalizes: the checks are data (deploy/deployment-rules.json), covering

    required environment variables · required bind mounts · feature flags · secrets · configuration files

so a NEW dependency needs a new rule, not new code.

Usage:
    deploy/ops/validate-deployment.py            # validate every stack in the rules file
    deploy/ops/validate-deployment.py --rules deploy/deployment-rules.json

Exit 0 = every declared capability has its dependencies wired; exit 1 = at least one is missing, with a
human-readable explanation (which service, what's missing, why it matters, and how to fix it).

Requires `docker compose` (to render the authoritative merged model, incl. the !reset/!override tags).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TRUTHY = {"1", "true", "yes", "on"}

# Dummy values for the override's fail-fast ${VAR:?} guards, so `config` can render the merged model.
# They are structural placeholders only — the checks below never treat them as real secrets.
DUMMY_ENV = {
    "RWE_INTERNAL_SECRET": "ci", "NEXTAUTH_URL": "https://ci.example.com", "NEXTAUTH_SECRET": "ci",
    "GOOGLE_CLIENT_ID": "ci", "GOOGLE_CLIENT_SECRET": "ci", "BETA_ALLOWLIST": "ci@example.com",
}


def render(files: list[str]) -> dict:
    """Return the merged compose model as a dict (docker compose config --format json)."""
    cmd = ["docker", "compose"]
    for f in files:
        cmd += ["-f", f]
    cmd += ["config", "--format", "json"]
    env = {**os.environ, **DUMMY_ENV}
    out = subprocess.run(cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"`{' '.join(cmd)}` failed:\n{out.stderr.strip()}")
    return json.loads(out.stdout)


def raw_text(files: list[str]) -> str:
    return "\n".join(open(os.path.join(REPO_ROOT, f), encoding="utf-8").read() for f in files)


def env_of(svc: dict) -> dict:
    e = svc.get("environment") or {}
    if isinstance(e, list):  # list form "KEY=val" -> dict (compose usually renders a map, but be safe)
        out = {}
        for item in e:
            k, _, v = str(item).partition("=")
            out[k] = v
        return out
    return {k: ("" if v is None else str(v)) for k, v in e.items()}


def mount_targets(svc: dict) -> set[str]:
    return {m.get("target") for m in (svc.get("volumes") or []) if isinstance(m, dict) and m.get("target")}


def secret_status(key: str, raw: str) -> str:
    """How `key` is set across the raw compose file(s): 'interpolated' (${VAR}), 'hardcoded' (a literal),
    or 'absent'. Commented lines are ignored. Interpolation means the value comes from deploy/.env."""
    interpolated = hardcoded = False
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        m = re.match(rf"{re.escape(key)}:\s*(.*)$", s)
        if not m:
            continue
        val = m.group(1).strip()
        if val.startswith("${"):
            interpolated = True
        elif val:
            hardcoded = True
    if hardcoded:
        return "hardcoded"
    return "interpolated" if interpolated else "absent"


def when_holds(when: dict, svc: dict) -> bool:
    env = env_of(svc)
    if "env_truthy" in when and env.get(when["env_truthy"], "").strip().lower() not in TRUTHY:
        return False
    for k, expected in (when.get("env_equals") or {}).items():
        if env.get(k) != expected:
            return False
    if "env_present" in when and when["env_present"] not in env:
        return False
    return True


class Finding:
    def __init__(self, rule, stack, service, kind, detail):
        self.rule, self.stack, self.service, self.kind, self.detail = rule, stack, service, kind, detail


def check_stack(stack: dict, rules: list, findings: list) -> None:
    files = stack["files"]
    config = render(files)
    raw = raw_text(files)
    services = config.get("services", {})
    for rule in rules:
        when = rule["when"]
        svc_name = when["service"]
        svc = services.get(svc_name)
        if svc is None or not when_holds(when, svc):
            continue  # capability not declared in this stack -> rule does not apply
        env, mounts = env_of(svc), mount_targets(svc)
        req = rule.get("require", {})
        for key in req.get("env", []):
            if key not in env:
                findings.append(Finding(rule, stack, svc_name, "required env", f"`{key}` is not set on the service"))
        for tgt in req.get("mounts", []):
            if tgt not in mounts:
                findings.append(Finding(rule, stack, svc_name, "required mount", f"nothing is mounted at `{tgt}`"))
        for key in req.get("secrets", []):
            if key not in env:
                findings.append(Finding(rule, stack, svc_name, "secret", f"secret `{key}` is not wired to the service"))
            elif secret_status(key, raw) == "hardcoded":
                findings.append(Finding(rule, stack, svc_name, "secret", f"secret `{key}` is HARDCODED — use `${{{key}}}` from deploy/.env"))
        for path in req.get("files", []):
            if not os.path.exists(os.path.join(REPO_ROOT, path)):
                findings.append(Finding(rule, stack, svc_name, "config file", f"required config file `{path}` does not exist"))


def check_hardcoded_secrets(stack: dict, patterns: list, findings: list) -> None:
    """Global lint: any env key whose NAME looks like a secret must be interpolated, never a literal."""
    raw = raw_text(stack["files"])
    seen = set()
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        m = re.match(r"([A-Z][A-Z0-9_]*):\s*(\S.*)$", s)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key in seen or val.startswith("${") or val.startswith("!"):
            continue
        if any(p in key for p in patterns):
            seen.add(key)
            findings.append(Finding({"id": "no-hardcoded-secrets", "why": "A secret committed to the compose file is exposed in git history and to anyone with repo access; secrets must come from deploy/.env via ${VAR} interpolation.", "fix": f"Replace the literal with `{key}: ${{{key}}}` and set the value in deploy/.env."},
                                     stack, "(compose)", "secret", f"`{key}` looks like a secret but is set to a literal value"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate deployment-dependency wiring across compose services.")
    ap.add_argument("--rules", default=os.path.join(REPO_ROOT, "deploy", "deployment-rules.json"))
    args = ap.parse_args()

    spec = json.load(open(args.rules, encoding="utf-8"))
    rules, stacks = spec["rules"], spec["stacks"]
    patterns = spec.get("secret_name_patterns", [])

    findings: list[Finding] = []
    print("== deployment-dependency validation ==")
    for stack in stacks:
        try:
            before = len(findings)
            check_stack(stack, rules, findings)
            check_hardcoded_secrets(stack, patterns, findings)
            status = "FAIL" if len(findings) > before else "ok"
            print(f"  [{status}] {stack['name']}  ({' + '.join(stack['files'])})")
        except Exception as e:  # a render failure is itself a validation failure
            print(f"  [ERROR] {stack['name']}: {e}")
            findings.append(Finding({"id": "compose-render", "why": "The merged compose model could not be rendered.", "fix": "Fix the compose syntax / interpolation error above."}, stack, "-", "render", str(e)))

    if not findings:
        n = sum(1 for _ in rules)
        print(f"\nPASS — all {n} deployment-dependency rules satisfied across {len(stacks)} stack(s). "
              "Every declared capability has its env / mounts / secrets / config files wired.")
        return 0

    print(f"\nFAIL — {len(findings)} deployment-dependency problem(s):\n")
    for i, f in enumerate(findings, 1):
        r = f.rule
        print(f"  {i}. [{r['id']}] {f.kind} — service '{f.service}' in {f.stack['name']}")
        print(f"     Problem: {f.detail}.")
        print(f"     Why it matters: {r.get('why','').strip()}")
        if r.get("fix"):
            print(f"     Fix: {r['fix'].strip()}")
        print()
    print("A required deployment dependency is missing. Wire it (see each Fix above), then re-run "
          "`deploy/ops/validate-deployment.py`. Rules live in deploy/deployment-rules.json.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
