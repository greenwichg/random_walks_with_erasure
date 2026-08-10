"use client";

import { PeriodAnalytics } from "@/components/report/period-analytics";

/** Where "Monthly deep dive ready" lands (N3). See `components/report/period-analytics.tsx`. */
export default function MonthlyDeepDivePage() {
  return <PeriodAnalytics period="monthly" />;
}
