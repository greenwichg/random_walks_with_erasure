"use client";

import { useAnalytics } from "@/hooks/use-data";
import { PageContainer } from "@/components/layout/page-container";
import { SectionCard } from "@/components/shared/section-card";
import { TrendChart } from "@/components/shared/trend-chart";
import { StackedBar } from "@/components/shared/stacked-bar";
import { ErrorState } from "@/components/shared/states";
import { Skeleton } from "@/components/ui/skeleton";
import { EMOTION_META } from "@/lib/metrics";

export default function AnalyticsPage() {
  const { data, isLoading, isError, refetch } = useAnalytics();

  return (
    <PageContainer>
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          How your reading and Information Health have moved over the last 30 days.
        </p>
      </div>

      {isLoading && (
        <div className="grid gap-6 lg:grid-cols-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-64 rounded-lg" />
          ))}
        </div>
      )}
      {isError && <ErrorState onRetry={() => refetch()} />}

      {data && (
        <div className="grid gap-6 lg:grid-cols-2">
          <SectionCard title="Health improvement" info="Your overall Information Health score over time." className="lg:col-span-2">
            <TrendChart data={data.healthImprovement} height={240} />
          </SectionCard>

          <SectionCard title="Reading volume" info="Articles read per day.">
            <StackedBar
              data={data.readingOverTime}
              series={[{ key: "overall", label: "Articles", color: "hsl(var(--primary))" }]}
              stacked={false}
              height={220}
            />
          </SectionCard>

          <SectionCard title="Topic diversity" info="Breadth of subjects over time.">
            <TrendChart data={data.topicDiversity} height={220} color="hsl(var(--primary))" domain={[0, 100]} />
          </SectionCard>

          <SectionCard title="Political diversity" info="How balanced your left/right reading has been.">
            <TrendChart data={data.politicalDiversity} height={220} color="hsl(var(--center))" domain={[0, 100]} />
          </SectionCard>

          <SectionCard title="Publisher diversity" info="How many distinct outlets you rely on.">
            <TrendChart data={data.publisherDiversity} height={220} color="hsl(var(--left))" domain={[0, 100]} />
          </SectionCard>

          <SectionCard title="Emotional tone" info="The emotional makeup of your reading, by period.">
            <StackedBar
              data={data.emotion}
              series={(Object.keys(EMOTION_META) as (keyof typeof EMOTION_META)[]).map((k) => ({
                key: k,
                label: EMOTION_META[k].label,
                color: EMOTION_META[k].color,
              }))}
              percent
              height={220}
            />
          </SectionCard>

          <SectionCard title="Reporting vs opinion" info="The balance of factual reporting and commentary.">
            <StackedBar
              data={data.reporting}
              series={[
                { key: "reporting", label: "Reporting", color: "hsl(var(--positive))" },
                { key: "opinion", label: "Opinion", color: "hsl(var(--muted-foreground))" },
              ]}
              percent
              height={220}
            />
          </SectionCard>

          <SectionCard title="Recommendation acceptance" info="How often you act on our recommendations.">
            <StackedBar
              data={data.recommendationAcceptance}
              series={[
                { key: "accepted", label: "Accepted", color: "hsl(var(--positive))" },
                { key: "ignored", label: "Ignored", color: "hsl(var(--muted))" },
              ]}
              stacked={false}
              height={220}
            />
          </SectionCard>
        </div>
      )}
    </PageContainer>
  );
}
