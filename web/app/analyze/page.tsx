import type { Metadata } from "next";
import { AnalyzerFlow } from "@/components/analyzer/analyzer-flow";

export const metadata: Metadata = {
  title: "Analyze an article",
  description:
    "Paste a news-article URL to see its Information Health analysis — political lean, topic, tone, and cross-publisher story context. No account needed; nothing is saved.",
};

/** Public Article Analyzer (A2): anonymous, read-only analysis of one news URL. */
export default function AnalyzePage() {
  return <AnalyzerFlow />;
}
