"use client";

import { PeriodAnalytics } from "@/components/report/period-analytics";

/** Where "Weekly report ready" lands (N3). See `components/report/period-analytics.tsx`. */
export default function WeeklyReportPage() {
  return <PeriodAnalytics period="weekly" />;
}
