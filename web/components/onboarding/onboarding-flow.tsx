"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowLeft, ArrowLeftRight, Check, Loader2, Search, Sparkles } from "lucide-react";
import type { EstimateHealthReport, LeanBucket, Outlet } from "@/types/domain";
import { Button } from "@/components/ui/button";
import { ScoreRing } from "@/components/shared/score-ring";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { PENDING_ONBOARDING_KEY } from "@/components/onboarding/onboarding-sync";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type Step = "welcome" | "pick" | "building" | "estimate";

const MIN_PICKS = 3;
const BUILD_LINE_KEYS = [
  "onboarding.loading.scoring",
  "onboarding.loading.blindSpots",
  "onboarding.loading.picking",
];
const LEAN_HUE: Record<LeanBucket, string> = {
  left: "hsl(var(--left))",
  center: "hsl(var(--center))",
  right: "hsl(var(--right))",
};

const fade = {
  initial: { opacity: 0, y: 12 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -8 },
  transition: { duration: 0.35, ease: [0.16, 1, 0.3, 1] as const },
};

/**
 * The public onboarding flow: value → pick outlets → build → the Initial Information Health
 * Estimate. The estimate is always labeled (never presented as measured); the "Save" CTA is
 * the progressive account step (Google sign-in), asked only after the user has seen value.
 */
export function OnboardingFlow() {
  const [step, setStep] = React.useState<Step>("welcome");
  const [outlets, setOutlets] = React.useState<Outlet[]>([]);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [estimate, setEstimate] = React.useState<EstimateHealthReport | null>(null);
  const [error, setError] = React.useState(false);

  React.useEffect(() => {
    let alive = true;
    fetch("/api/outlets")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: Outlet[]) => {
        if (alive) setOutlets(Array.isArray(data) ? data : []);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const toggle = React.useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const build = React.useCallback(async (ids: string[]) => {
    setError(false);
    setEstimate(null);
    setStep("building");
    try {
      const res = await fetch("/api/estimate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ outlets: ids }),
      });
      if (!res.ok) throw new Error("estimate failed");
      setEstimate((await res.json()) as EstimateHealthReport);
    } catch {
      setError(true);
    }
  }, []);

  // Hold on the "building" beat briefly once the estimate lands, then reveal it.
  React.useEffect(() => {
    if (step === "building" && estimate) {
      const t = setTimeout(() => setStep("estimate"), 900);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [step, estimate]);

  const sample = React.useCallback(() => {
    const spread = pickSpread(outlets, 6);
    setSelected(new Set(spread));
    void build(spread);
  }, [outlets, build]);

  return (
    <main className="relative min-h-screen overflow-hidden bg-background">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 [background:radial-gradient(60%_50%_at_50%_-10%,hsl(var(--primary)/0.10),transparent)]"
      />
      <div className="relative mx-auto flex min-h-screen max-w-md flex-col justify-center px-5 py-10">
        <AnimatePresence mode="wait">
          {step === "welcome" && (
            <Welcome key="welcome" onBuild={() => setStep("pick")} onSample={sample} />
          )}
          {step === "pick" && (
            <Pick
              key="pick"
              outlets={outlets}
              selected={selected}
              onToggle={toggle}
              onBack={() => setStep("welcome")}
              onBuild={() => build([...selected])}
            />
          )}
          {step === "building" && (
            <Building
              key="building"
              error={error}
              onRetry={() => build([...selected])}
              onBack={() => setStep("pick")}
            />
          )}
          {step === "estimate" && estimate && (
            <Estimate
              key="estimate"
              report={estimate}
              outletIds={[...selected]}
              onAdjust={() => setStep("pick")}
            />
          )}
        </AnimatePresence>
      </div>
    </main>
  );
}

function Frame({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <motion.div {...fade} className={cn("rounded-2xl border bg-card p-7 shadow-sm", className)}>
      {children}
    </motion.div>
  );
}

/** Brand mark. `label` defaults to the (untranslated) product name; callers pass a localized label. */
function Brand({ label = "Information Health" }: { label?: string }) {
  return (
    <div className="mb-5 flex items-center gap-2">
      <span className="grid h-8 w-8 place-items-center rounded-lg bg-primary text-primary-foreground shadow">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden>
          <path
            d="M3 12h3.5l2-5 3 10 2.5-7 1.5 2H21"
            stroke="currentColor"
            strokeWidth="2.1"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span className="text-sm font-semibold tracking-tight">{label}</span>
    </div>
  );
}

function Steps({ n }: { n: 1 | 2 | 3 }) {
  const { t } = useTranslation();
  return (
    <div className="mb-5 flex items-center gap-1.5">
      {[1, 2, 3].map((i) => (
        <span key={i} className={cn("h-1.5 flex-1 rounded-full", i <= n ? "bg-primary" : "bg-muted")} />
      ))}
      <span className="ml-1.5 font-mono text-[10px] text-muted-foreground">{t("onboarding.stepOf", { n })}</span>
    </div>
  );
}

function Welcome({ onBuild, onSample }: { onBuild: () => void; onSample: () => void }) {
  const { t } = useTranslation();
  const values = [
    { icon: <Sparkles className="h-3.5 w-3.5" />, t: t("onboarding.value1.t"), d: t("onboarding.value1.d") },
    { icon: <Search className="h-3.5 w-3.5" />, t: t("onboarding.value2.t"), d: t("onboarding.value2.d") },
    { icon: <ArrowLeftRight className="h-3.5 w-3.5" />, t: t("onboarding.value3.t"), d: t("onboarding.value3.d") },
  ];
  return (
    <Frame>
      <Brand />
      <h1 className="text-balance text-2xl font-bold tracking-tight">{t("onboarding.welcome.title")}</h1>
      <p className="mt-2 text-sm text-muted-foreground">{t("onboarding.welcome.subtitle")}</p>
      <ul className="my-6 space-y-3">
        {values.map((v) => (
          <li key={v.t} className="flex gap-3">
            <span className="mt-0.5 grid h-6 w-6 flex-none place-items-center rounded-md bg-primary/10 text-primary">
              {v.icon}
            </span>
            <span>
              <span className="block text-sm font-medium">{v.t}</span>
              <span className="block text-xs text-muted-foreground">{v.d}</span>
            </span>
          </li>
        ))}
      </ul>
      <Button className="w-full" size="lg" onClick={onBuild}>
        {t("onboarding.buildReport")}
      </Button>
      <Button className="mt-2 w-full" size="lg" variant="outline" onClick={onSample}>
        {t("onboarding.sampleFirst")}
      </Button>
      <p className="mt-3 text-center text-xs text-muted-foreground">{t("onboarding.takesMinute")}</p>
      <p className="mt-4 text-center text-xs text-muted-foreground">
        {t("onboarding.haveAccount")}{" "}
        <button
          onClick={() => window.location.assign("/signin")}
          className="font-medium text-primary underline-offset-2 hover:underline"
        >
          {t("onboarding.signIn")}
        </button>
      </p>
    </Frame>
  );
}

function Pick({
  outlets,
  selected,
  onToggle,
  onBack,
  onBuild,
}: {
  outlets: Outlet[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onBack: () => void;
  onBuild: () => void;
}) {
  const { t } = useTranslation();
  const count = selected.size;
  const ok = count >= MIN_PICKS;
  return (
    <Frame>
      <Steps n={2} />
      <h2 className="text-xl font-bold tracking-tight">{t("onboarding.pick.title")}</h2>
      <p className="mt-1 text-sm text-muted-foreground">{t("onboarding.pick.subtitle")}</p>
      <div className="my-4 flex flex-wrap gap-2">
        {outlets.length === 0 && <p className="text-sm text-muted-foreground">{t("onboarding.loadingPublishers")}</p>}
        {outlets.map((o) => {
          const on = selected.has(o.id);
          return (
            <button
              key={o.id}
              onClick={() => onToggle(o.id)}
              aria-pressed={on}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs transition-colors",
                on ? "border-primary bg-primary/10 font-medium text-primary" : "border-border bg-card hover:bg-accent",
              )}
            >
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: LEAN_HUE[o.leanBucket] }} />
              {o.name}
              {on && <Check className="h-3 w-3" />}
            </button>
          );
        })}
      </div>
      <Button className="w-full" size="lg" disabled={!ok} onClick={onBuild}>
        {ok
          ? t("onboarding.buildPicked", { n: count })
          : t("onboarding.pickAtLeast", { n: MIN_PICKS })}
      </Button>
      <button
        onClick={onBack}
        className="mt-3 flex w-full items-center justify-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3 w-3" /> {t("common.back")}
      </button>
      <p className="mt-3 text-center text-[11px] text-muted-foreground">{t("onboarding.autoHint")}</p>
    </Frame>
  );
}

function Building({ error, onRetry, onBack }: { error: boolean; onRetry: () => void; onBack: () => void }) {
  const { t } = useTranslation();
  const [line, setLine] = React.useState(0);
  React.useEffect(() => {
    if (error) return undefined;
    const id = setInterval(() => setLine((l) => (l + 1) % BUILD_LINE_KEYS.length), 1100);
    return () => clearInterval(id);
  }, [error]);

  if (error) {
    return (
      <Frame className="text-center">
        <h2 className="text-lg font-semibold">{t("onboarding.error.title")}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{t("onboarding.error.body")}</p>
        <Button className="mt-5 w-full" size="lg" onClick={onRetry}>
          {t("common.tryAgain")}
        </Button>
        <button onClick={onBack} className="mt-3 text-xs text-muted-foreground hover:text-foreground">
          {t("onboarding.backToPicks")}
        </button>
      </Frame>
    );
  }
  return (
    <Frame className="text-center">
      <Steps n={3} />
      <div className="mx-auto mt-4 grid h-28 w-28 place-items-center">
        <Loader2 className="h-16 w-16 animate-spin text-primary" />
      </div>
      <p className="mt-5 text-lg font-semibold">{t("onboarding.readingDiet")}</p>
      <p className="mt-1 font-mono text-xs text-muted-foreground">{t(BUILD_LINE_KEYS[line] ?? "onboarding.loading.scoring")}</p>
    </Frame>
  );
}

function Estimate({
  report,
  outletIds,
  onAdjust,
}: {
  report: EstimateHealthReport;
  outletIds: string[];
  onAdjust: () => void;
}) {
  const { t } = useTranslation();
  const takeaway = report.improvements[0];
  // Stash the selection so it survives the sign-in redirect; OnboardingSync persists it post-auth.
  const save = () => {
    try {
      window.localStorage.setItem(PENDING_ONBOARDING_KEY, JSON.stringify({ outlets: outletIds }));
    } catch {
      /* ignore storage failures — sign-in still proceeds */
    }
    // Go to the sign-in page (Google, or the demo login when enabled) rather than forcing one
    // provider — so a reader without Google configured can still sign in.
    window.location.assign("/signin");
  };
  return (
    <Frame>
      <Brand label={t("onboarding.estimateTitle")} />
      <div className="mb-4 inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-700 dark:text-amber-400">
        <Sparkles className="h-3 w-3" /> {t("onboarding.estimateBadge")}
      </div>
      <div className="flex flex-col items-center">
        <ScoreRing score={report.overall} band={report.band} label={t("common.of100")} size={132} />
        {report.band && (
          <span className="mt-2 rounded-full bg-muted px-3 py-1 font-mono text-[11px] font-semibold uppercase tracking-wide">
            {t(`band.${report.band}`)}
          </span>
        )}
      </div>
      <SpectrumBar distribution={report.viewpoint} className="mt-5" />
      {takeaway && (
        <div className="mt-5 rounded-xl border bg-muted/50 p-3.5 text-sm">
          <span className="mb-1 block font-mono text-[10px] font-bold uppercase tracking-wide text-primary">{t("onboarding.yourOneThing")}</span>
          {takeaway.detail}
        </div>
      )}
      <div className="mt-4 rounded-xl border border-dashed p-3.5 text-xs text-muted-foreground">
        <span className="mb-1.5 block font-medium text-foreground">{t("onboarding.whatThis")}</span>
        <ul className="space-y-1">
          <li>• {t("onboarding.what1")}</li>
          <li>• {t("onboarding.what2")}</li>
          <li>• {t("onboarding.what3")}</li>
        </ul>
      </div>
      <Button className="mt-5 w-full" size="lg" onClick={save}>
        {t("onboarding.saveTrack")}
      </Button>
      <button
        onClick={onAdjust}
        className="mt-3 flex w-full items-center justify-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3 w-3" /> {t("onboarding.adjustOutlets")}
      </button>
      <p className="mt-3 text-center text-[11px] text-muted-foreground">{t("onboarding.privacyNote")}</p>
    </Frame>
  );
}

/** A left/center/right spread of outlet ids, so the "sample" estimate is illustrative. */
function pickSpread(outlets: Outlet[], n: number): string[] {
  const buckets: LeanBucket[] = ["left", "center", "right"];
  const out: string[] = [];
  for (let round = 0; round < n && out.length < n; round++) {
    for (const b of buckets) {
      const inBucket = outlets.filter((o) => o.leanBucket === b);
      const pick = inBucket[round];
      if (pick && !out.includes(pick.id)) out.push(pick.id);
      if (out.length >= n) break;
    }
  }
  return out.length ? out : outlets.slice(0, n).map((o) => o.id);
}
