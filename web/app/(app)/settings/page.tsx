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
  RotateCcw,
} from "lucide-react";
import type { Settings } from "@/types/domain";
import { useSettings, useUpdateSettings } from "@/hooks/use-data";
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

/** Descriptor for the political-openness slider (maps to per-request RWE-B epsilon; 50 = engine default). */
function opennessLabel(v: number) {
  if (v < 25) return "Stay close to my views";
  if (v < 55) return "Nudge me gently";
  if (v < 80) return "Challenge me regularly";
  return "Push me out of my comfort zone";
}

/** Descriptor for recommendation strength (maps to per-request RWE-D beta; 50 = engine default). */
function strengthLabel(v: number) {
  if (v < 25) return "Subtle";
  if (v < 55) return "Balanced";
  if (v < 80) return "Assertive";
  return "Bold";
}

export default function SettingsPage() {
  const { data, isLoading, isError, refetch } = useSettings();
  const updateSettings = useUpdateSettings();
  const { theme, setTheme } = useTheme();
  const { t } = useTranslation();

  // Local draft seeded once from the server; theme is applied live via next-themes.
  const [draft, setDraft] = React.useState<Settings | null>(null);
  const [saved, setSaved] = React.useState(false);

  React.useEffect(() => {
    if (data && !draft) setDraft(data);
  }, [data, draft]);

  const dirty = React.useMemo(
    () => !!data && !!draft && JSON.stringify(data) !== JSON.stringify({ ...draft, theme: data.theme }),
    [data, draft],
  );

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setDraft((d) => (d ? { ...d, [key]: value } : d));
    setSaved(false);
  }
  function setNotif<K extends keyof Settings["notifications"]>(key: K, value: boolean) {
    setDraft((d) => (d ? { ...d, notifications: { ...d.notifications, [key]: value } } : d));
    setSaved(false);
  }
  function setPrivacy<K extends keyof Settings["privacy"]>(key: K, value: boolean) {
    setDraft((d) => (d ? { ...d, privacy: { ...d.privacy, [key]: value } } : d));
    setSaved(false);
  }

  function save() {
    if (!draft) return;
    // Persist to the engine; sync the draft to the normalised server result so the form is clean.
    updateSettings.mutate(draft, {
      onSuccess: (persisted) => {
        setDraft(persisted);
        setSaved(true);
      },
    });
  }
  function reset() {
    if (data) setDraft(data);
    setSaved(false);
  }

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">{t("settings.title")}</h1>
        <p className="mt-1 text-sm text-muted-foreground">{t("settings.subtitle")}</p>
      </div>

      {isLoading && (
        <div className="grid gap-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-56 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {draft && (
        <div className="grid gap-6 pb-24">
          {/* Appearance */}
          <SectionCard title={t("settings.appearance")} info="How the app looks. Theme changes apply instantly.">
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
                      onClick={() => setTheme(opt.value)}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
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
                        "rounded-full border px-3 py-1 text-sm font-medium transition-colors",
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
            title="Recommendations"
            info="These directly tune the RWE recommender that picks your reads."
          >
            <div className="space-y-8">
              <SliderRow
                icon={Scale}
                title="Political openness"
                description="How far across the spectrum we reach for cross-cutting reads."
                value={draft.politicalOpenness}
                onChange={(v) => set("politicalOpenness", v)}
                valueLabel={opennessLabel(draft.politicalOpenness)}
              />
              <SliderRow
                icon={Sparkles}
                title="Recommendation strength"
                description="How aggressively we diversify away from your usual diet."
                value={draft.recommendationStrength}
                onChange={(v) => set("recommendationStrength", v)}
                valueLabel={strengthLabel(draft.recommendationStrength)}
              />
              <SliderRow
                icon={Target}
                title="Daily reading goal"
                description="Your target reading time. Tracks today's progress on your dashboard."
                value={draft.readingGoalMinutes}
                onChange={(v) => set("readingGoalMinutes", v)}
                min={5}
                max={120}
                step={5}
                valueLabel={`${draft.readingGoalMinutes} min/day`}
              />
            </div>
          </SectionCard>

          {/* Reports */}
          <SectionCard title="Reports" info="Your recurring Information Health summaries.">
            <div className="divide-y">
              <ToggleRow
                icon={FileText}
                title="Weekly report"
                description="A snapshot of your reading diet, every Monday."
                checked={draft.weeklyReport}
                onChange={(v) => set("weeklyReport", v)}
              />
              <ToggleRow
                icon={FileText}
                title="Monthly deep dive"
                description="A fuller breakdown with trends and blind spots."
                checked={draft.monthlyReport}
                onChange={(v) => set("monthlyReport", v)}
              />
            </div>
          </SectionCard>

          {/* Notifications */}
          <SectionCard title="Notifications" info="What we ping you about.">
            <div className="divide-y">
              <ToggleRow
                icon={Bell}
                title="New recommendations"
                description="When fresh cross-cutting reads are ready for you."
                checked={draft.notifications.recommendations}
                onChange={(v) => setNotif("recommendations", v)}
              />
              <ToggleRow
                icon={Bell}
                title="Weekly digest"
                description="A short email rounding up your week."
                checked={draft.notifications.weeklyDigest}
                onChange={(v) => setNotif("weeklyDigest", v)}
              />
              <ToggleRow
                icon={Bell}
                title="Streak reminders"
                description="A nudge before your reading streak lapses."
                checked={draft.notifications.streakReminders}
                onChange={(v) => setNotif("streakReminders", v)}
              />
              <ToggleRow
                icon={Bell}
                title="Blind-spot alerts"
                description="When a topic or viewpoint drops out of your diet."
                checked={draft.notifications.blindSpotAlerts}
                onChange={(v) => setNotif("blindSpotAlerts", v)}
              />
            </div>
          </SectionCard>

          {/* Privacy */}
          <SectionCard title="Privacy" info="You're in control of your data.">
            <div className="divide-y">
              <ToggleRow
                icon={ShieldCheck}
                title="Share anonymized metrics"
                description="Help improve population benchmarks. Never tied to your identity."
                checked={draft.privacy.shareAnonymizedMetrics}
                onChange={(v) => setPrivacy("shareAnonymizedMetrics", v)}
              />
              <ToggleRow
                icon={ShieldCheck}
                title="Personalized ads"
                description="Off by default. We don't sell your reading history."
                checked={draft.privacy.personalizedAds}
                onChange={(v) => setPrivacy("personalizedAds", v)}
              />
            </div>
          </SectionCard>

          {/* Browser extension — real per-user tokens (talks to the engine, not mock) */}
          <ExtensionConnect />
        </div>
      )}

      {/* Floating save bar */}
      <AnimatePresence>
        {(dirty || saved) && (
          <motion.div
            initial={{ y: 80, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 80, opacity: 0 }}
            transition={{ type: "spring", stiffness: 400, damping: 32 }}
            className="fixed inset-x-0 bottom-4 z-30 flex justify-center px-4 lg:pl-64"
          >
            <div className="glass flex w-full max-w-md items-center justify-between gap-3 rounded-full border px-3 py-2 shadow-card">
              {saved ? (
                <span className="flex items-center gap-2 pl-2 text-sm font-medium text-positive">
                  <Check className="h-4 w-4" /> {t("common.allChangesSaved")}
                </span>
              ) : (
                <span className="flex items-center gap-2 pl-2 text-sm text-muted-foreground">
                  <span className="h-2 w-2 rounded-full bg-caution" /> {t("settings.unsaved")}
                </span>
              )}
              <div className="flex items-center gap-2">
                {!saved && (
                  <Button variant="ghost" size="sm" onClick={reset} className="text-muted-foreground">
                    <RotateCcw className="h-3.5 w-3.5" /> {t("common.reset")}
                  </Button>
                )}
                <Button size="sm" onClick={save} disabled={saved} className="rounded-full">
                  {saved ? t("settings.savedShort") : t("settings.saveChanges")}
                </Button>
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
