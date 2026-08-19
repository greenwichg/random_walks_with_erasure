"use client";

import * as React from "react";
import { motion } from "framer-motion";
import {
  Sparkles,
  Route,
  Flame,
  Newspaper,
  HeartPulse,
  Eye,
  BookmarkCheck,
  Trophy,
  CalendarDays,
  TrendingUp,
  Lock,
  type LucideIcon,
} from "lucide-react";
import type { Achievement, Profile } from "@/types/domain";
import { useProfile } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { SectionCard } from "@/components/shared/section-card";
import { TrendChart } from "@/components/shared/trend-chart";
import { DeltaBadge } from "@/components/shared/delta-badge";
import { ErrorState } from "@/components/shared/states";
import { Card, CardContent } from "@/components/ui/card";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import { activeLang, formatDate } from "@/lib/i18n-core";

const ACHIEVEMENT_ICONS: Record<string, LucideIcon> = {
  sparkles: Sparkles,
  route: Route,
  flame: Flame,
  newspaper: Newspaper,
  "heart-pulse": HeartPulse,
  eye: Eye,
};

const fmtDate = (iso: string) =>
  formatDate(iso, activeLang(), { month: "long", day: "numeric", year: "numeric" });

function initials(name: string) {
  return name
    .split(" ")
    .map((n) => n[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export default function ProfilePage() {
  const { data, isLoading, isError, refetch } = useProfile();
  const { t } = useTranslation();

  if (isLoading) return <ProfileSkeleton />;
  if (isError || !data)
    return (
      <PageContainer>
        <ErrorState onRetry={() => refetch()} />
      </PageContainer>
    );

  const latest = data.scoreHistory[data.scoreHistory.length - 1]?.overall ?? 0;
  const first = data.scoreHistory[0]?.overall ?? latest;
  const journeyDelta = latest - first;
  const unlocked = data.achievements.filter((a) => a.unlocked);
  const locked = data.achievements.filter((a) => !a.unlocked);

  return (
    <PageContainer>
      {/* Hero */}
      <Card className="mb-6 overflow-hidden">
        <div className="h-28 bg-gradient-to-r from-primary/25 via-lean-center/20 to-lean-right/20" />
        <CardContent className="pt-0">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div className="flex items-end gap-4">
              <Avatar className="relative z-10 -mt-12 h-24 w-24 border-4 border-card shadow-card">
                <AvatarImage src={data.avatarUrl ?? ""} alt={data.name} />
                <AvatarFallback className="bg-primary text-2xl font-semibold text-primary-foreground">
                  {initials(data.name)}
                </AvatarFallback>
              </Avatar>
              <div className="pb-1">
                <h1 className="text-2xl font-semibold tracking-tight">{data.name}</h1>
                <p className="text-sm text-muted-foreground">@{data.handle}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 pb-1 text-sm text-muted-foreground">
              <CalendarDays className="h-4 w-4" />
              {t("profile.memberSince", { date: fmtDate(data.joinedAt) })}
            </div>
          </div>

          {/* Quick stats */}
          <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-3">
            <QuickStat icon={Flame} label={t("profile.currentStreak")} value={t("profile.daysValue", { n: data.streakDays })} accent="caution" />
            <QuickStat icon={TrendingUp} label={t("profile.longestStreak")} value={t("profile.daysValue", { n: data.longestStreak })} accent="positive" />
            <QuickStat icon={BookmarkCheck} label={t("profile.saved")} value={data.savedCount} accent="primary" />
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Health journey */}
        <SectionCard
          title={t("profile.healthJourney")}
          info={t("profile.healthJourneyInfo")}
          className="lg:col-span-2"
          action={
            <div className="flex items-center gap-2">
              <span className="text-2xl font-semibold tabular-nums">{latest}</span>
              <DeltaBadge value={journeyDelta} />
            </div>
          }
        >
          <TrendChart data={data.scoreHistory} height={240} domain={[0, 100]} />
        </SectionCard>

        {/* Streak */}
        <SectionCard title={t("profile.readingStreak")} info={t("profile.readingStreakInfo")}>
          <div className="flex flex-col items-center py-2 text-center">
            <div className="relative grid h-28 w-28 place-items-center">
              <Flame className="h-14 w-14 text-caution" strokeWidth={1.5} />
              <span className="absolute -bottom-1 rounded-full bg-caution/15 px-3 py-0.5 text-lg font-semibold tabular-nums text-caution">
                {data.streakDays}
              </span>
            </div>
            <p className="mt-4 text-sm font-medium">{t("profile.dayStreak", { n: data.streakDays })}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {t("profile.streakHint", { n: data.longestStreak })}
            </p>
            <div className="mt-5 flex flex-wrap justify-center gap-1.5">
              {Array.from({ length: 14 }).map((_, i) => {
                const active = i >= 14 - Math.min(14, data.streakDays);
                return (
                  <span
                    key={i}
                    className={cn(
                      "h-2.5 w-2.5 rounded-full",
                      active ? "bg-caution" : "bg-muted",
                    )}
                  />
                );
              })}
            </div>
          </div>
        </SectionCard>
      </div>

      {/* Achievements */}
      <div className="mt-6">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Trophy className="h-5 w-5 text-caution" />
            <h2 className="text-lg font-semibold tracking-tight">{t("profile.achievements")}</h2>
          </div>
          <span className="text-sm text-muted-foreground">
            {t("profile.unlockedCount", { unlocked: unlocked.length, total: data.achievements.length })}
          </span>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[...unlocked, ...locked].map((a, i) => (
            <AchievementCard key={a.id} achievement={a} index={i} />
          ))}
        </div>
      </div>

      {/* Milestones */}
      <div className="mt-8">
        <h2 className="mb-4 text-lg font-semibold tracking-tight">{t("profile.milestones")}</h2>
        <Milestones profile={data} />
      </div>
    </PageContainer>
  );
}

/* ------------------------------------------------------------------ */

function QuickStat({
  icon: Icon,
  label,
  value,
  accent,
}: {
  icon: LucideIcon;
  label: string;
  value: string | number;
  accent: "primary" | "positive" | "caution" | "left";
}) {
  const tint: Record<string, string> = {
    primary: "bg-primary/10 text-primary",
    positive: "bg-positive/12 text-positive",
    caution: "bg-caution/12 text-caution",
    left: "bg-lean-left/12 text-lean-left",
  };
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card/60 p-3">
      <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg", tint[accent])}>
        <Icon className="h-[1.1rem] w-[1.1rem]" />
      </span>
      <div className="min-w-0">
        <p className="text-lg font-semibold leading-tight tabular-nums">{value}</p>
        <p className="truncate text-xs text-muted-foreground">{label}</p>
      </div>
    </div>
  );
}

function AchievementCard({ achievement: a, index }: { achievement: Achievement; index: number }) {
  const { t } = useTranslation();
  const Icon = ACHIEVEMENT_ICONS[a.icon] ?? Trophy;
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.04, 0.3) }}
      className={cn(
        "relative flex gap-4 rounded-lg border p-4 transition-colors",
        a.unlocked ? "bg-card" : "bg-muted/30",
      )}
    >
      <span
        className={cn(
          "grid h-12 w-12 shrink-0 place-items-center rounded-xl",
          a.unlocked ? "bg-caution/15 text-caution" : "bg-muted text-muted-foreground",
        )}
      >
        {a.unlocked ? <Icon className="h-6 w-6" /> : <Lock className="h-5 w-5" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className={cn("font-medium", !a.unlocked && "text-muted-foreground")}>{a.title}</p>
        <p className="mt-0.5 text-sm text-muted-foreground">{a.description}</p>
        {a.unlocked ? (
          a.unlockedAt && (
            <p className="mt-2 text-xs font-medium text-positive">{t("profile.unlockedOn", { date: fmtDate(a.unlockedAt) })}</p>
          )
        ) : (
          <div className="mt-3">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary/70"
                style={{ width: `${Math.round((a.progress ?? 0) * 100)}%` }}
              />
            </div>
            <p className="mt-1.5 text-xs text-muted-foreground">
              {t("profile.percentComplete", { pct: Math.round((a.progress ?? 0) * 100) })}
            </p>
          </div>
        )}
      </div>
    </motion.div>
  );
}

/** A simple vertical timeline built from join date + unlocked achievements. */
function Milestones({ profile }: { profile: Profile }) {
  const { t } = useTranslation();
  const items = [
    { icon: Sparkles, title: t("profile.joined"), date: profile.joinedAt },
    ...profile.achievements
      .filter((a) => a.unlocked && a.unlockedAt)
      .map((a) => ({ icon: ACHIEVEMENT_ICONS[a.icon] ?? Trophy, title: a.title, date: a.unlockedAt! })),
  ].sort((x, y) => +new Date(x.date) - +new Date(y.date));

  return (
    <Card>
      <CardContent className="py-6">
        <ol className="relative space-y-6 border-l border-border pl-6">
          {items.map((m, i) => (
            <motion.li
              key={i}
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: Math.min(i * 0.05, 0.4) }}
              className="relative"
            >
              <span className="absolute -left-[1.9rem] grid h-7 w-7 place-items-center rounded-full border bg-card text-muted-foreground">
                <m.icon className="h-3.5 w-3.5" />
              </span>
              <p className="text-sm font-medium">{m.title}</p>
              <p className="text-xs text-muted-foreground">{fmtDate(m.date)}</p>
            </motion.li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function ProfileSkeleton() {
  return (
    <PageContainer>
      <Skeleton className="mb-6 h-56 rounded-lg" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Skeleton className="h-72 rounded-lg lg:col-span-2" />
        <Skeleton className="h-72 rounded-lg" />
      </div>
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-28 rounded-lg" />
        ))}
      </div>
    </PageContainer>
  );
}
