import type {
  AnalyticsSeries,
  Article,
  CoachMessage,
  DashboardSummary,
  EmotionShare,
  MeasuredHealthReport,
  HistoryEntry,
  Profile,
  Recommendation,
  Settings,
  Story,
  TrendPoint,
} from "@/types/domain";
import { dominantEmotion, leanBucket } from "@/lib/political";
import { PUBLISHERS, publisherLean } from "@/mock/publishers";

/* ------------------------------------------------------------------ *
 * Deterministic PRNG so the mock dataset is stable across requests.
 * ------------------------------------------------------------------ */
function mulberry32(seed: number) {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = mulberry32(42);
const pick = <T>(arr: T[]): T => arr[Math.floor(rand() * arr.length)]!;
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

function emotionFor(lean: number, register: string): EmotionShare {
  const charged = Math.abs(lean) * 0.25 + (register === "opinion" ? 0.2 : 0);
  const raw = {
    fear: 0.12 + charged * rand(),
    outrage: 0.12 + charged * rand(),
    analysis: 0.3 + (register === "reporting" ? 0.25 : 0) * rand(),
    positive: 0.14 * rand(),
    neutral: 0.18 * rand(),
  };
  const total = Object.values(raw).reduce((a, b) => a + b, 0);
  return {
    fear: raw.fear / total,
    outrage: raw.outrage / total,
    analysis: raw.analysis / total,
    positive: raw.positive / total,
    neutral: raw.neutral / total,
  };
}

const TOPICS = [
  "Elections",
  "Economy",
  "Climate",
  "Foreign Policy",
  "Healthcare",
  "Technology",
  "Immigration",
  "Supreme Court",
  "Education",
  "Energy",
];

const HEADLINES: Record<string, string[]> = {
  Elections: [
    "Turnout surges in early voting as both parties court suburbs",
    "New poll shows a tightening race in key swing states",
    "What the latest ballot measures reveal about the electorate",
    "Campaigns pour record sums into a handful of battlegrounds",
    "Election officials brace for a long night of counting",
    "Independent voters emerge as the decisive bloc",
  ],
  Economy: [
    "Inflation cools for a third straight month, easing pressure on the Fed",
    "Job growth beats forecasts but wage gains stay muted",
    "Why economists disagree on the strength of the recovery",
    "Consumer spending holds up despite higher borrowing costs",
    "Markets rally as the Fed signals a patient path on rates",
    "Small businesses feel the squeeze even as headline numbers improve",
  ],
  Climate: [
    "Nations edge toward a deal on climate finance at summit",
    "Record heat renews debate over the pace of the energy transition",
    "How coastal cities are budgeting for rising seas",
    "Clean-energy investment hits a new high, but grids lag behind",
    "Farmers adapt as shifting seasons upend planting",
    "The fight over who pays for climate damage intensifies",
  ],
  "Foreign Policy": [
    "Diplomats push for a ceasefire as talks resume",
    "Allies weigh new sanctions amid escalating tensions",
    "The strategic calculus behind the latest defense pact",
    "Aid convoys stall as negotiators trade blame",
    "A fragile truce holds through its first week",
    "Regional powers jockey for influence after the summit",
  ],
  Healthcare: [
    "Drug pricing rules take effect, and the industry pushes back",
    "Rural hospitals warn of closures under new funding formula",
    "A new study reframes the debate over preventive care",
    "Insurers and providers clash over surprise-billing rules",
    "Mental-health coverage expands, but access gaps remain",
    "Lawmakers spar over the future of public-health funding",
  ],
  Technology: [
    "Regulators open a fresh antitrust probe into AI platforms",
    "Chipmakers race to meet surging demand for data centers",
    "Inside the fight over who controls your digital identity",
    "New rules would force platforms to open their algorithms",
    "A wave of layoffs reshapes the tech labor market",
    "Startups bet big on AI as funding rebounds",
  ],
  Immigration: [
    "Border crossings shift as new policy reshapes the flow",
    "Cities strain to house new arrivals as funding lags",
    "The economic case for and against the latest visa plan",
    "Courts weigh a challenge to the asylum overhaul",
    "Employers press for more work visas amid labor shortages",
    "Communities divided over how to welcome newcomers",
  ],
  "Supreme Court": [
    "Justices hear arguments in a case that could reshape agencies",
    "A narrow ruling leaves the bigger question for another term",
    "How the court's term is redrawing the balance of power",
    "Dissent warns of sweeping consequences for regulation",
    "The court sidesteps a major constitutional question",
    "A divided bench signals more clashes ahead",
  ],
  Education: [
    "Test scores rebound unevenly as districts spend relief funds",
    "The quiet fight over what belongs in the curriculum",
    "Colleges rethink admissions after the affirmative-action ruling",
    "Teacher shortages force districts to get creative",
    "Student-debt relief faces a fresh legal test",
    "Parents and boards clash over classroom policy",
  ],
  Energy: [
    "Grid operators warn of strain as demand from AI climbs",
    "A permitting overhaul could unlock stalled clean-energy projects",
    "Why gas prices are diverging across regions",
    "Utilities race to add capacity before summer peaks",
    "The nuclear revival gains unlikely allies",
    "Battery costs fall, reshaping the case for renewables",
  ],
};

function makeArticle(i: number, forcedTopic?: string, forcedPublisher?: string): Article {
  const topic = forcedTopic ?? pick(TOPICS);
  const publisher = forcedPublisher ?? pick(PUBLISHERS).name;
  const houseLean = publisherLean(publisher);
  const lean = Math.max(-2, Math.min(2, houseLean + (rand() - 0.5) * 0.8));
  const registerRoll = rand();
  const register = registerRoll > 0.62 ? "opinion" : registerRoll > 0.28 ? "reporting" : "mixed";
  const emotion = emotionFor(lean, register);
  const headlines = HEADLINES[topic] ?? ["Untitled story"];
  return {
    id: `art_${i}`,
    headline: headlines[i % headlines.length]!,
    publisher,
    publisherLean: houseLean,
    topic,
    lean,
    leanBucket: leanBucket(lean),
    confidence: 0.55 + rand() * 0.42,
    emotion,
    dominantEmotion: dominantEmotion(emotion),
    register,
    publishedAt: daysAgo(rand() * 6),
    readingMinutes: 2 + Math.floor(rand() * 8),
  };
}

export const ARTICLES: Article[] = Array.from({ length: 48 }, (_, i) => makeArticle(i));

/* ------------------------------------------------------------------ *
 * Trend series (last 30 days), converging upward toward a healthier diet.
 * ------------------------------------------------------------------ */
function trend(base: number, target: number, jitter = 4): TrendPoint[] {
  return Array.from({ length: 30 }, (_, i) => {
    const t = i / 29;
    const value = base + (target - base) * t + (rand() - 0.5) * jitter;
    return { date: daysAgo(29 - i), overall: Math.round(Math.max(0, Math.min(100, value))) };
  });
}
const OVERALL_TREND = trend(58, 72);

/* ------------------------------------------------------------------ *
 * The flagship Information Health Report.
 * ------------------------------------------------------------------ */
export const REPORT: MeasuredHealthReport = {
  mode: "measured",
  coverage: { reads: 42, threshold: 5, sufficient: true },
  overall: 72,
  overallDelta: 6,
  updatedAt: new Date().toISOString(),
  axisConfidence: 0.74,
  // Coverage pilot: the Viewpoint dimension's dimensional coverage (partial-coverage example).
  viewpointCoverage: {
    eligiblePoliticalReads: 28,
    authoritativeLeanReads: 24,
    unknownLeanReads: 4,
    provenance: "outlet_registry",
  },
  metrics: [
    { key: "topicDiversity", score: 78, delta: 4, raw: { value: 12, unit: "topics" }, benchmark: 61 },
    { key: "sourceDiversity", score: 64, delta: 9, raw: { value: 14, unit: "sources" }, benchmark: 52 },
    { key: "reportingRatio", score: 58, delta: -3, raw: { value: 0.58, unit: "P(reporting)" }, benchmark: 55 },
    { key: "emotionalBalance", score: 51, delta: 7, raw: { value: 0.62, unit: "calm share" }, benchmark: 60 },
    { key: "echoChamber", score: 69, delta: 5, raw: { value: 0.71, unit: "balance" }, benchmark: 48 },
    { key: "viewpointBalance", score: 61, delta: 8, raw: { value: 0.33, unit: "cross-cutting" }, benchmark: 40 },
    { key: "openMindedness", score: 82, delta: 3, raw: { value: 0.82, unit: "click-through" }, benchmark: 57 },
    { key: "confidence", score: 74, delta: 1, raw: { value: 0.74, unit: "axis margin" }, benchmark: 70 },
  ],
  viewpoint: { left: 0.46, center: 0.22, right: 0.32 },
  attention: { fear: 0.19, outrage: 0.17, analysis: 0.34, positive: 0.15, neutral: 0.15 },
  topics: [
    { topic: "Economy", share: 0.22, count: 34 },
    { topic: "Elections", share: 0.18, count: 28 },
    { topic: "Technology", share: 0.14, count: 22 },
    { topic: "Climate", share: 0.12, count: 19 },
    { topic: "Foreign Policy", share: 0.1, count: 15 },
    { topic: "Healthcare", share: 0.08, count: 12 },
    { topic: "Supreme Court", share: 0.06, count: 9 },
    { topic: "Immigration", share: 0.05, count: 8 },
    { topic: "Energy", share: 0.03, count: 5 },
    { topic: "Education", share: 0.02, count: 3 },
  ],
  sources: PUBLISHERS.slice(0, 9).map((p, i) => ({
    source: p.name,
    share: [0.16, 0.13, 0.12, 0.11, 0.1, 0.09, 0.08, 0.06, 0.04][i] ?? 0.03,
    count: [24, 20, 18, 16, 15, 13, 12, 9, 6][i] ?? 4,
    lean: p.lean,
  })),
  blindSpots: [
    { topic: "Energy", gap: 0.72, note: "You rarely read energy coverage — a growing driver of the economy debate." },
    { topic: "Right-leaning economics", gap: 0.61, note: "Your economic reading skews left; the WSJ/Dispatch view is under-represented." },
    { topic: "Education", gap: 0.55, note: "Almost absent from your diet this month." },
  ],
  improvements: [
    {
      id: "imp_1",
      title: "Add two cross-cutting reads a week",
      detail: "You lean left on the economy. Two center-right economics pieces would lift Viewpoint Balance the most.",
      metric: "viewpointBalance",
      impact: 8,
      trigger: "Your political reading is 64% left, 22% center, 14% right.",
      evidence: "Your reading leans left; the other side is thin.",
      suggestedAction: "Adding a couple of right-leaning reads would balance your viewpoints.",
      expectedBenefit: "Can improve your Viewpoint Balance.",
      evidenceBasis: [
        { field: "viewpoint", label: "left", value: 0.64 },
        { field: "viewpoint", label: "center", value: 0.22 },
        { field: "viewpoint", label: "right", value: 0.14 },
      ],
      impactEstimate: {
        low: 4,
        high: 7,
        method: "simulated",
        metric: "viewpointBalance",
        confidence: "medium",
        fromScore: 38,
        toScore: { low: 42, high: 45 },
        explanation:
          "Simulated: taking this step would move your Viewpoint Balance percentile from 38 to about 42–45 (+4–7).",
      },
      ranking: { rank: 1, visible: true, priority: -29, reason: null, signals: [{ signal: "metricScore", effect: "base priority" }] },
    },
    {
      id: "imp_2",
      title: "Swap one outrage read for analysis",
      detail: "A third of your reading is fear/outrage. Trading one charged piece a day for analysis raises Emotional Balance.",
      metric: "emotionalBalance",
      impact: 6,
      trigger: "33% of your reading leans on fear and outrage.",
      evidence: "Fear 19% and outrage 14%; analysis is 21%.",
      suggestedAction: "Swapping one charged read a day for calm analysis raises the balance.",
      expectedBenefit: "Can improve your Emotional Balance.",
      evidenceBasis: [
        { field: "attention", label: "fear", value: 0.19 },
        { field: "attention", label: "outrage", value: 0.14 },
        { field: "attention", label: "analysis", value: 0.21 },
      ],
      impactEstimate: {
        low: 3,
        high: 6,
        method: "simulated",
        metric: "emotionalBalance",
        confidence: "high",
        fromScore: 45,
        toScore: { low: 48, high: 51 },
        explanation:
          "Simulated: taking this step would move your Emotional Balance percentile from 45 to about 48–51 (+3–6).",
      },
      ranking: { rank: 2, visible: true, priority: -28, reason: null, signals: [{ signal: "metricScore", effect: "base priority" }] },
    },
    {
      id: "imp_3",
      title: "Broaden beyond your top outlets",
      detail: "41% of your reading comes from three outlets. Two new sources would meaningfully lift Source Diversity.",
      metric: "sourceDiversity",
      impact: 5,
      trigger: "72% of your reading came from Reuters and BBC.",
      evidence: "Reuters (41%) and BBC (31%) account for most of your reading.",
      suggestedAction: "Reading from an outlet beyond Reuters and BBC would widen your sources.",
      expectedBenefit: "Can broaden your Source Diversity.",
      evidenceBasis: [
        { field: "sources", label: "Reuters", value: 0.41 },
        { field: "sources", label: "BBC", value: 0.31 },
      ],
      impactEstimate: {
        low: 5,
        high: 8,
        method: "simulated",
        metric: "sourceDiversity",
        confidence: "medium",
        fromScore: 34,
        toScore: { low: 39, high: 42 },
        explanation:
          "Simulated: taking this step would move your Source Diversity percentile from 34 to about 39–42 (+5–8).",
      },
      ranking: { rank: 3, visible: true, priority: -27, reason: null, signals: [{ signal: "metricScore", effect: "base priority" }] },
    },
  ],
};

/* ------------------------------------------------------------------ *
 * Recommendations — RWE surfaces bridging + diversifying reads.
 * ------------------------------------------------------------------ */
export const RECOMMENDATIONS: Recommendation[] = ARTICLES.slice(0, 14).map((article, i): Recommendation => {
  const crossCutting = article.leanBucket === "right";
  return {
    article,
    strategy: crossCutting ? "rwe-b" : i % 3 === 0 ? "rwe-d" : "adaptive",
    // Mirrors the Evidence Resolver vocabulary (21a.3): reason == explanation.message.
    reason: crossCutting
      ? "This article offers another political perspective."
      : `You rarely read ${article.publisher}. This broadens your source diversity.`,
    helpsMetric: crossCutting ? "viewpointBalance" : i % 2 === 0 ? "sourceDiversity" : "topicDiversity",
    crossCutting,
    explanation: crossCutting
      ? { type: "bridge", priority: 4, message: "This article offers another political perspective." }
      : {
          type: "new_publisher",
          priority: 3,
          message: `You rarely read ${article.publisher}. This broadens your source diversity.`,
        },
  };
});

/* ------------------------------------------------------------------ *
 * Reading history (last ~20 reads).
 * ------------------------------------------------------------------ */
export const HISTORY: HistoryEntry[] = ARTICLES.slice(10, 40).map((article, i) => ({
  id: `hist_${i}`,
  article,
  readAt: daysAgo(rand() * 14),
  readingMinutes: article.readingMinutes,
  completed: rand() > 0.25,
})).sort((a, b) => +new Date(b.readAt) - +new Date(a.readAt));

/* ------------------------------------------------------------------ *
 * Stories — one event, coverage across the spectrum.
 * ------------------------------------------------------------------ */
function makeStory(id: string, title: string, topic: string, summary: string): Story {
  const outlets = [...PUBLISHERS].sort(() => rand() - 0.5).slice(0, 6);
  // Each outlet covers the same event with a distinct framing (headlines are
  // assigned by index so no two outlets in a cluster share a headline).
  const pool = HEADLINES[topic] ?? [title];
  const coverage = outlets.map((p, idx) => {
    const lean = Math.max(-2, Math.min(2, p.lean + (rand() - 0.5) * 0.5));
    const register = rand() > 0.5 ? "reporting" : "opinion";
    return {
      publisher: p.name,
      headline: pool[idx % pool.length]!,
      lean,
      leanBucket: leanBucket(lean),
      register: register as "reporting" | "opinion",
      emotion: emotionFor(lean, register),
      publishedAt: daysAgo(rand() * 2),
    };
  });
  const dist = coverage.reduce(
    (acc, c) => {
      acc[c.leanBucket] += 1;
      return acc;
    },
    { left: 0, center: 0, right: 0 },
  );
  const total = coverage.length;
  return {
    id,
    title,
    summary,
    topic,
    updatedAt: daysAgo(rand()),
    totalCoverage: 40 + Math.floor(rand() * 120),
    distribution: { left: dist.left / total, center: dist.center / total, right: dist.right / total },
    coverage,
    timeline: [
      { date: daysAgo(2), label: "Story breaks" },
      { date: daysAgo(1), label: "Officials respond" },
      { date: daysAgo(0.2), label: "Analysis and reaction" },
    ],
    blindspotSide: dist.right <= 1 ? "right" : dist.left <= 1 ? "left" : undefined,
  };
}

export const STORIES: Story[] = [
  makeStory("story_1", "Fed holds rates as inflation cools", "Economy", "The central bank kept rates steady, citing easing inflation but a still-strong labor market. Coverage splits on what it signals for the recovery."),
  makeStory("story_2", "Climate summit reaches finance framework", "Climate", "Negotiators announced a framework for climate finance. Reporting differs sharply on whether the pledges are meaningful."),
  makeStory("story_3", "Supreme Court hears major agency-power case", "Supreme Court", "Justices weighed a case that could curb federal agencies. Outlets diverge on the stakes for regulation."),
  makeStory("story_4", "New AI antitrust probe opens", "Technology", "Regulators opened an antitrust investigation into leading AI platforms, drawing praise and pushback."),
];

/* ------------------------------------------------------------------ *
 * Dashboard summary.
 * ------------------------------------------------------------------ */
export const DASHBOARD: DashboardSummary = {
  overall: REPORT.overall,
  overallDelta: REPORT.overallDelta,
  trend: OVERALL_TREND,
  today: {
    articlesRead: 7,
    avgReadingMinutes: 4.6,
    politicalShare: 0.42,
    topTopics: ["Economy", "Elections", "Technology"],
  },
  metrics: REPORT.metrics,
  streakDays: 12,
};

/* ------------------------------------------------------------------ *
 * Analytics series.
 * ------------------------------------------------------------------ */
export const ANALYTICS: AnalyticsSeries = {
  readingOverTime: Array.from({ length: 30 }, (_, i) => ({
    date: daysAgo(29 - i),
    overall: 3 + Math.round(rand() * 9),
  })),
  topicDiversity: trend(60, 78, 3),
  politicalDiversity: trend(44, 61, 3),
  publisherDiversity: trend(50, 64, 3),
  emotion: Array.from({ length: 12 }, (_, i) => ({
    date: daysAgo((11 - i) * 2.5),
    fear: 0.2 + (rand() - 0.5) * 0.08,
    outrage: 0.18 + (rand() - 0.5) * 0.08,
    analysis: 0.32 + (rand() - 0.5) * 0.08,
    positive: 0.15 + (rand() - 0.5) * 0.05,
    neutral: 0.15 + (rand() - 0.5) * 0.05,
  })),
  reporting: Array.from({ length: 12 }, (_, i) => ({
    date: daysAgo((11 - i) * 2.5),
    reporting: 0.55 + (rand() - 0.5) * 0.12,
    opinion: 0.45 + (rand() - 0.5) * 0.12,
  })),
  recommendationAcceptance: Array.from({ length: 12 }, (_, i) => ({
    date: daysAgo((11 - i) * 2.5),
    accepted: 3 + Math.floor(rand() * 6),
    ignored: 1 + Math.floor(rand() * 4),
  })),
  healthImprovement: OVERALL_TREND,
};

/* ------------------------------------------------------------------ *
 * Profile + settings.
 * ------------------------------------------------------------------ */
export const PROFILE: Profile = {
  name: "Alex Rivera",
  handle: "alexr",
  email: "alex@example.com",
  joinedAt: daysAgo(96),
  streakDays: 12,
  longestStreak: 21,
  scoreHistory: OVERALL_TREND,
  savedCount: 34,
  achievements: [
    { id: "ach_1", title: "First Steps", description: "Read your first 10 articles", icon: "sparkles", unlocked: true, unlockedAt: daysAgo(90) },
    { id: "ach_2", title: "Bridge Builder", description: "Read 25 cross-cutting articles", icon: "route", unlocked: true, unlockedAt: daysAgo(30) },
    { id: "ach_3", title: "Steady Reader", description: "Maintain a 7-day streak", icon: "flame", unlocked: true, unlockedAt: daysAgo(20) },
    { id: "ach_4", title: "Wide Lens", description: "Read from 20 different publishers", icon: "newspaper", unlocked: false, progress: 0.7 },
    { id: "ach_5", title: "Calm Mind", description: "Keep Emotional Balance above 70 for a week", icon: "heart-pulse", unlocked: false, progress: 0.45 },
    { id: "ach_6", title: "Blind Spot Slayer", description: "Clear 5 blind spots", icon: "eye", unlocked: false, progress: 0.4 },
  ],
};

// Kept in sync with the engine's settings_service.DEFAULT_SETTINGS (S1.5) so the dev mock fallback
// and production expose identical defaults — a signed-in reader who's never saved sees the same
// values whether the engine is up or the mock stands in for it.
export const SETTINGS: Settings = {
  theme: "system",
  language: "en",
  politicalOpenness: 50,
  recommendationStrength: 50,
  readingGoalMinutes: 20,
  weeklyReport: true,
  monthlyReport: false,
  notifications: {
    recommendations: true,
    weeklyDigest: true,
    streakReminders: false,
    blindSpotAlerts: false,
  },
  // `privacy` intentionally omitted — it's absent from the frontend Settings type (nothing reads
  // it). The engine still stores/returns it; a live response's extra key is ignored structurally.
};

/* ------------------------------------------------------------------ *
 * AI Coach seed conversation + canned grounded replies.
 * ------------------------------------------------------------------ */
export const COACH_GREETING: CoachMessage = {
  id: "msg_0",
  role: "assistant",
  createdAt: new Date().toISOString(),
  content:
    "Hi Alex — I'm your Information Health coach. I can explain any of your metrics, spot patterns in your reading, and suggest ways to balance your diet. What would you like to look at?",
};

/** A grounded canned reply (mock). The real coach calls the Python narrate service. */
export function coachReply(prompt: string): CoachMessage {
  const p = prompt.toLowerCase();
  const base = { id: `msg_${Date.now()}`, role: "assistant" as const, createdAt: new Date().toISOString() };
  if (p.includes("echo") || p.includes("one-sided") || p.includes("balanced")) {
    return {
      ...base,
      content:
        "Your Echo Chamber score is 69/100 — better than most readers (median 48). Your reading runs 46% left, 32% right, 22% center, so you do hear both sides, just tilted left. The quickest lift is on the economy, where you read almost entirely left-leaning outlets. Want two center-right economics reads to balance it?",
      citations: [
        { metric: "echoChamber", value: 69 },
        { metric: "viewpointBalance", value: 61 },
      ],
      suggestions: RECOMMENDATIONS.filter((r) => r.crossCutting).slice(0, 2).map((r) => r.article),
    };
  }
  if (p.includes("improve") || p.includes("better") || p.includes("goal")) {
    return {
      ...base,
      content:
        "Three moves would lift your overall score the most this week: (1) add two cross-cutting economics reads (+8 Viewpoint Balance), (2) trade one outrage piece a day for analysis (+6 Emotional Balance), and (3) branch out from your top three publishers (+5 Source Diversity). Shall I set these as your weekly goals?",
      citations: [
        { metric: "viewpointBalance", value: 61 },
        { metric: "emotionalBalance", value: 51 },
      ],
    };
  }
  if (p.includes("suggest") || p.includes("article") || p.includes("recommend")) {
    return {
      ...base,
      content:
        "Here are two reads picked to widen your lens without whiplash — both are opposite-side but close to the centre, exactly the 'different, not too far' zone.",
      suggestions: RECOMMENDATIONS.filter((r) => r.crossCutting).slice(0, 2).map((r) => r.article),
    };
  }
  return {
    ...base,
    content:
      "Your overall Information Health is 72/100, up 6 points this month. Your strongest metric is Open-Mindedness (82) — when we show you the other side, you engage. Your biggest opportunity is Emotional Balance (51): a lot of your reading leans on fear and outrage. Ask me to explain any metric, or to suggest reads.",
    citations: [
      { metric: "openMindedness", value: 82 },
      { metric: "emotionalBalance", value: 51 },
    ],
  };
}
