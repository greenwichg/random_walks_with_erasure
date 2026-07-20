"use client";

import * as React from "react";
import { useTheme } from "next-themes";
import { motion, AnimatePresence } from "framer-motion";
import {
  Monitor,
  Moon,
  Sun,
  Check,
  Bell,
  Sparkles,
  Scale,
  Target,
  FileText,
  ShieldCheck,
  ExternalLink,
  RotateCcw,
  Loader2,
  AlertCircle,
} from "lucide-react";
import type { Settings } from "@/types/domain";
import { useSettings, useUpdateSettings } from "@/hooks/use-data";
import { diffSettings, hasChanges } from "@/lib/settings-diff";
import { useTranslation } from "@/lib/i18n";
import { PageContainer } from "@/components/layout/page-container";
import { SectionCard } from "@/components/shared/section-card";
import { ErrorState } from "@/components/shared/states";
import { ExtensionConnect } from "@/components/settings/extension-connect";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Slider } from "@/components/ui/slider";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "pt", label: "Português" },
];

/** Catalog key for the political-openness slider (maps to per-request RWE-B epsilon; 50 = default). */
function opennessLabelKey(v: number) {
  if (v < 25) return "settings.openness.close";
  if (v < 55) return "settings.openness.gentle";
  if (v < 80) return "settings.openness.regular";
  return "settings.openness.push";
}

/** Catalog key for recommendation strength (maps to per-request RWE-D beta; 50 = default). */
function strengthLabelKey(v: number) {
  if (v < 25) return "settings.strength.subtle";
  if (v < 55) return "settings.strength.balanced";
  if (v < 80) return "settings.strength.assertive";
  return "settings.strength.bold";
}

export default function SettingsPage() {
  const { data, isLoading, isError, refetch } = useSettings();
  const updateSettings = useUpdateSettings();
  const persistTheme = useUpdateSettings(); // theme's own write-through — separate from the Save button
  const { theme, setTheme } = useTheme();
  const { t } = useTranslation();

  // `base` is the server snapshot the draft was seeded from; `draft` is the reader's working copy.
  // Both are local (no new global state). Diffing the draft against `base` — not the live `data` —
  // is what makes the flow robust: a background change to a field the reader never touched neither
  // counts as "dirty" (no phantom "unsaved") nor gets reverted by the minimal patch.
  const [base, setBase] = React.useState<Settings | null>(null);
  const [draft, setDraft] = React.useState<Settings | null>(null);
  const [saved, setSaved] = React.useState(false);

  // The minimal PATCH the Save button would send: only the fields the reader changed vs their base,
  // theme excluded (it has its own instant write-through). One memo is the single source of truth
  // for both "is there anything to save?" (dirty) and what save() sends.
  const patch = React.useMemo<Partial<Settings>>(() => {
    if (!base || !draft) return {};
    const p = diffSettings(base, draft);
    delete p.theme; // owned by applyTheme — never the Save button
    return p;
  }, [base, draft]);
  const dirty = hasChanges(patch);

  // Seed base + draft from the server, and RESEED both on a background refetch — but only while the
  // page is clean. When the reader has edits (dirty), base AND draft are preserved, so their edits
  // are never clobbered and a concurrent server change to an untouched field stays out of the patch.
  React.useEffect(() => {
    if (!data) return;
    if (!base || !draft || !dirty) {
      setBase(data);
      setDraft(data);
    }
  }, [data, base, draft, dirty]);

  // Restore the account's saved theme ONCE per mount, only if this device currently shows something
  // different (next-themes reads localStorage). Runs client-side after both the settings and
  // next-themes are ready, so it never fights the device preference, never runs before hydration,
  // and — with the provider's disableTransitionOnChange — the cross-device apply is an instant swap,
  // not a fade. Same-device revisits match localStorage and no-op (no flicker).
  const appliedStoredTheme = React.useRef(false);
  React.useEffect(() => {
    if (appliedStoredTheme.current || !data?.theme || !theme) return;
    appliedStoredTheme.current = true;
    if (data.theme !== theme) setTheme(data.theme);
  }, [data?.theme, theme, setTheme]);

  // A theme click applies instantly (as before) AND writes through to the account — never via the
  // Save button. The mutation updates the settings cache on success, so `data.theme` stays current.
  function applyTheme(value: Settings["theme"]) {
    if (value === theme) return; // already applied (and already persisted) — skip the redundant write
    setTheme(value);
    persistTheme.mutate({ theme: value });
  }

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
    setSaved(false);
  }
  function setNotif<K extends keyof Settings["notifications"]>(key: K, value: boolean) {
    setDraft((d) => (d ? { ...d, notifications: { ...d.notifications, [key]: value } } : d));
    setSaved(false);
  }

  function save() {
    if (!hasChanges(patch)) return; // empty diff → no request
    // Send only the minimal patch; adopt the normalised server result as the new base + draft (which
    // carries the current persisted theme) so the form goes clean. Reuses the existing mutation.
    updateSettings.mutate(patch, {
      onSuccess: (persisted) => {
        setBase(persisted);
        setDraft(persisted);
        setSaved(true);
      },
    });
  }
  function reset() {
    if (base) setDraft(base); // discard edits back to the editing base
    setSaved(false);
  }

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{t("settings.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("settings.subtitle")}</p>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 gap-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-56 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {draft && (
        <div className="grid grid-cols-1 gap-6 pb-24">
          {/* Appearance */}
          <SectionCard title={t("settings.appearance")} info={t("settings.appearanceInfo")}>
            <div className="divide-y">
              <SettingRow title={t("settings.theme")} description={t("settings.themeDesc")}>
                <div className="inline-flex rounded-lg border bg-muted p-1">
                  {(
                    [
                      { value: "light", labelKey: "settings.theme.light", icon: Sun },
                      { value: "dark", labelKey: "settings.theme.dark", icon: Moon },
                      { value: "system", labelKey: "settings.theme.system", icon: Monitor },
                    ] as const
                  ).map((opt) => (
                    <button
                      key={opt.value}
                      onClick={() => applyTheme(opt.value)}
                      className={cn(
                        "touch-target inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                        theme === opt.value
                          ? "bg-background text-foreground shadow-soft"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      <opt.icon className="h-4 w-4" /> {t(opt.labelKey)}
                    </button>
                  ))}
                </div>
              </SettingRow>
              <SettingRow title={t("settings.language")} description={t("settings.languageDesc")}>
                <div className="flex flex-wrap justify-end gap-1.5">
                  {LANGUAGES.map((l) => (
                    <button
                      key={l.value}
                      onClick={() => set("language", l.value)}
                      className={cn(
                        "touch-target inline-flex items-center justify-center rounded-full border px-3 py-1 text-sm font-medium transition-colors",
                        draft.language === l.value
                          ? "border-primary/40 bg-primary/10 text-primary"
                          : "text-muted-foreground hover:bg-accent",
                      )}
                    >
                      {l.label}
                    </button>
                  ))}
                </div>
              </SettingRow>
            </div>
          </SectionCard>

          {/* Recommendations */}
          <SectionCard
            title={t("settings.recommendations")}
            info={t("settings.recommendationsInfo")}
          >
            <div className="space-y-8">
              <SliderRow
                icon={Scale}
                title={t("settings.politicalOpenness")}
                description={t("settings.opennessDesc")}
                value={draft.politicalOpenness}
                onChange={(v) => set("politicalOpenness", v)}
                valueLabel={t(opennessLabelKey(draft.politicalOpenness))}
              />
              <SliderRow
                icon={Sparkles}
                title={t("settings.recommendationStrength")}
                description={t("settings.strengthDesc")}
                value={draft.recommendationStrength}
                onChange={(v) => set("recommendationStrength", v)}
                valueLabel={t(strengthLabelKey(draft.recommendationStrength))}
              />
              <SliderRow
                icon={Target}
                title={t("settings.readingGoal")}
                description={t("settings.readingGoalFull")}
                value={draft.readingGoalMinutes}
                onChange={(v) => set("readingGoalMinutes", v)}
                min={5}
                max={120}
                step={5}
                valueLabel={t("settings.perDay", { n: draft.readingGoalMinutes })}
              />
            </div>
          </SectionCard>

          {/* Reports */}
          <SectionCard title={t("settings.reports")} info={t("settings.reportsInfo")}>
            <div className="divide-y">
              <ToggleRow
                icon={FileText}
                title={t("settings.weeklyReport")}
                description={t("settings.weeklyReportDesc")}
                checked={draft.weeklyReport}
                onChange={(v) => set("weeklyReport", v)}
              />
              <ToggleRow
                icon={FileText}
                title={t("settings.monthlyReport")}
                description={t("settings.monthlyReportDesc")}
                checked={draft.monthlyReport}
                onChange={(v) => set("monthlyReport", v)}
              />
            </div>
          </SectionCard>

          {/* Notifications */}
          <SectionCard title={t("settings.notifications")} info={t("settings.notificationsInfo")}>
            <div className="divide-y">
              <ToggleRow
                icon={Bell}
                title={t("settings.notif.recs")}
                description={t("settings.notif.recsDesc")}
                checked={draft.notifications.recommendations}
                onChange={(v) => setNotif("recommendations", v)}
              />
              <ToggleRow
                icon={Bell}
                title={t("settings.notif.digest")}
                description={t("settings.notif.digestDesc")}
                checked={draft.notifications.weeklyDigest}
                onChange={(v) => setNotif("weeklyDigest", v)}
              />
              <ToggleRow
                icon={Bell}
                title={t("settings.notif.streak")}
                description={t("settings.notif.streakDesc")}
                checked={draft.notifications.streakReminders}
                onChange={(v) => setNotif("streakReminders", v)}
              />
              <ToggleRow
                icon={Bell}
                title={t("settings.notif.blindSpot")}
                description={t("settings.notif.blindSpotDesc")}
                checked={draft.notifications.blindSpotAlerts}
                onChange={(v) => setNotif("blindSpotAlerts", v)}
              />
            </div>
          </SectionCard>

          {/* Privacy & data — the two former toggles (share-anonymized-metrics, personalized-ads)
              were removed: neither was consumed by any behavior, and an "ads" toggle contradicted
              our published privacy policy. The section now links to that policy, the honest source
              of truth for what we collect. */}
          <SectionCard title={t("settings.privacy")} info={t("settings.privacyInfo")}>
            <a
              href="/privacy"
              target="_blank"
              rel="noopener noreferrer"
              className="-mx-2 flex items-center justify-between gap-4 rounded-lg px-2 py-3 transition-colors hover:bg-accent"
            >
              <div className="flex min-w-0 items-start gap-3">
                <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                  <ShieldCheck className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-medium">{t("settings.privacy.policy")}</p>
                  <p className="mt-0.5 text-sm text-muted-foreground">{t("settings.privacy.policyDesc")}</p>
                </div>
              </div>
              <ExternalLink className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            </a>
          </SectionCard>

          {/* Browser extension — real per-user tokens (talks to the engine, not mock) */}
          <ExtensionConnect />
        </div>
      )}

      {/* Floating save bar — reflects the whole save lifecycle: saving, failed (+ Retry), saved,
          and unsaved. Save/Reset are disabled while a save is pending; theme is never involved. */}
      <AnimatePresence>
        {(dirty || saved || updateSettings.isPending || updateSettings.isError) && (
          <motion.div
            initial={{ y: 80, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 80, opacity: 0 }}
            transition={{ type: "spring", stiffness: 400, damping: 32 }}
            className="fixed inset-x-0 bottom-[max(1rem,env(safe-area-inset-bottom))] z-30 flex justify-center px-4 lg:pl-64"
          >
            <div
              role="status"
              aria-live="polite"
              className="glass flex w-full max-w-md items-center justify-between gap-3 rounded-full border px-3 py-2 shadow-card"
            >
              {updateSettings.isPending ? (
                <span className="flex items-center gap-2 pl-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {t("settings.saving")}
                </span>
              ) : updateSettings.isError ? (
                <span className="flex items-center gap-2 pl-2 text-sm font-medium text-negative">
                  <AlertCircle className="h-4 w-4" aria-hidden /> {t("settings.saveFailed")}
                </span>
              ) : saved ? (
                <span className="flex items-center gap-2 pl-2 text-sm font-medium text-positive">
                  <Check className="h-4 w-4" aria-hidden /> {t("common.allChangesSaved")}
                </span>
              ) : (
                <span className="flex items-center gap-2 pl-2 text-sm text-muted-foreground">
                  <span className="h-2 w-2 rounded-full bg-caution" /> {t("settings.unsaved")}
                </span>
              )}

              <div className="flex items-center gap-2">
                {updateSettings.isError ? (
                  <Button size="sm" onClick={save} className="rounded-full">
                    {t("settings.retry")}
                  </Button>
                ) : saved ? null : (
                  <>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={reset}
                      disabled={updateSettings.isPending}
                      className="text-muted-foreground"
                    >
                      <RotateCcw className="h-3.5 w-3.5" /> {t("common.reset")}
                    </Button>
                    <Button
                      size="sm"
                      onClick={save}
                      disabled={updateSettings.isPending}
                      className="rounded-full"
                    >
                      {updateSettings.isPending ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
                      {t("settings.saveChanges")}
                    </Button>
                  </>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </PageContainer>
  );
}

/* ------------------------------------------------------------------ *
 * Row primitives
 * ------------------------------------------------------------------ */
function SettingRow({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-medium">{title}</p>
        {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function ToggleRow({
  icon: Icon,
  title,
  description,
  checked,
  onChange,
}: {
  icon: React.ElementType;
  title: string;
  description?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-4 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-start gap-3">
        <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
          <Icon className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <p className="text-sm font-medium">{title}</p>
          {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      <Switch checked={checked} onCheckedChange={onChange} aria-label={title} />
    </div>
  );
}

function SliderRow({
  icon: Icon,
  title,
  description,
  value,
  onChange,
  valueLabel,
  min = 0,
  max = 100,
  step = 1,
}: {
  icon: React.ElementType;
  title: string;
  description?: string;
  value: number;
  onChange: (v: number) => void;
  valueLabel: string;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <div>
      <div className="mb-3 flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
            <Icon className="h-4 w-4" />
          </span>
          <div>
            <p className="text-sm font-medium">{title}</p>
            {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
          </div>
        </div>
        <span className="shrink-0 rounded-full bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">
          {valueLabel}
        </span>
      </div>
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => onChange(v ?? min)}
        className="pl-11"
      />
    </div>
  );
}
