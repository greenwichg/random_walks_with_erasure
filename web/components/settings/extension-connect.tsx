"use client";

import * as React from "react";
import { useSession } from "next-auth/react";
import { Puzzle, Copy, Check, Trash2, KeyRound, ShieldCheck, Loader2 } from "lucide-react";
import type { ApiTokenMint } from "@/types/domain";
import { useApiTokens, useCreateApiToken, useRevokeApiToken } from "@/hooks/use-data";
import { SectionCard } from "@/components/shared/section-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/**
 * "Connect Browser Extension" — mint / copy / list / revoke the per-user API tokens the
 * Information Health browser extension uses to sync reading. The plaintext token is shown once
 * (only its hash is stored); it authenticates the extension to this app's /api/me/reads, which
 * forwards to the engine. No reading data or scoring lives here — the backend stays the source
 * of truth.
 */
export function ExtensionConnect() {
  const { status } = useSession();
  const authed = status === "authenticated";
  const tokens = useApiTokens();
  const create = useCreateApiToken();
  const revoke = useRevokeApiToken();

  const [label, setLabel] = React.useState("");
  const [minted, setMinted] = React.useState<ApiTokenMint | null>(null);
  const [copied, setCopied] = React.useState(false);

  async function onCreate() {
    try {
      const res = await create.mutateAsync(label.trim() || undefined);
      setMinted(res);
      setCopied(false);
      setLabel("");
    } catch {
      /* surfaced via create.isError below */
    }
  }

  async function onCopy() {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted.token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — the token is visible to select manually */
    }
  }

  return (
    <SectionCard
      title="Connect browser extension"
      info="Generate a token, paste it into the Information Health extension, and the articles you read on supported news sites sync automatically."
    >
      {!authed ? (
        <p className="text-sm text-muted-foreground">
          Sign in to generate a token for the browser extension.
        </p>
      ) : (
        <div className="space-y-5">
          <div className="flex items-start gap-3 rounded-lg bg-muted/50 p-3">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              The extension sends the pages you open on supported news sites to your account. Tokens
              are shown once and stored only as a hash — revoke any token here to disconnect a device.
            </p>
          </div>

          {/* Freshly minted token — shown a single time */}
          {minted && (
            <div className="rounded-lg border border-primary/30 bg-primary/5 p-3">
              <div className="mb-2 flex items-center gap-1.5 text-sm font-medium text-primary">
                <KeyRound className="h-4 w-4" /> Your new token — copy it now
              </div>
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded-md border bg-background px-2.5 py-1.5 font-mono text-xs">
                  {minted.token}
                </code>
                <Button size="sm" variant="outline" onClick={onCopy} className="shrink-0">
                  {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                Paste this into the extension&apos;s setup screen. You won&apos;t be able to see it again —
                if you lose it, revoke it and generate a new one.
              </p>
            </div>
          )}

          {/* Mint a token */}
          <div className="flex flex-col gap-2 sm:flex-row">
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Label (optional, e.g. “Work laptop”)"
              maxLength={100}
              aria-label="Token label"
            />
            <Button onClick={onCreate} disabled={create.isPending} className="shrink-0">
              {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Puzzle className="h-4 w-4" />}
              Generate token
            </Button>
          </div>
          {create.isError && (
            <p className="text-sm text-negative">Couldn&apos;t generate a token. Please try again.</p>
          )}

          {/* Existing tokens */}
          <div>
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Active tokens
            </p>
            {tokens.isLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-12 rounded-lg" />
                <Skeleton className="h-12 rounded-lg" />
              </div>
            ) : tokens.data && tokens.data.length > 0 ? (
              <ul className="divide-y rounded-lg border">
                {tokens.data.map((t) => (
                  <li key={t.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{t.label || "Extension token"}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        Created {fmtDate(t.createdAt)} ·{" "}
                        {t.lastUsedAt ? `last used ${fmtDate(t.lastUsedAt)}` : "never used"}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {!t.lastUsedAt && <Badge variant="secondary">Unused</Badge>}
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-muted-foreground hover:text-negative"
                        onClick={() => revoke.mutate(t.id)}
                        disabled={revoke.isPending}
                        aria-label={`Revoke ${t.label || "token"}`}
                      >
                        <Trash2 className="h-3.5 w-3.5" /> Revoke
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                No tokens yet. Generate one to connect the extension.
              </p>
            )}
          </div>
        </div>
      )}
    </SectionCard>
  );
}
