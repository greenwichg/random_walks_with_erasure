import type { Lean } from "../domain/types.ts";

/**
 * A small AllSides-style publisher → house-lean table. In production this comes
 * from the backend's outlet-lean service; here it seeds coherent mock data.
 */
export const PUBLISHERS: { name: string; lean: Lean }[] = [
  { name: "The Associated Press", lean: -0.1 },
  { name: "Reuters", lean: 0.0 },
  { name: "BBC News", lean: -0.3 },
  { name: "The New York Times", lean: -1.1 },
  { name: "The Washington Post", lean: -1.0 },
  { name: "CNN", lean: -1.2 },
  { name: "NPR", lean: -0.8 },
  { name: "The Guardian", lean: -1.3 },
  { name: "Vox", lean: -1.5 },
  { name: "Politico", lean: -0.4 },
  { name: "The Wall Street Journal", lean: 0.6 },
  { name: "The Economist", lean: -0.2 },
  { name: "Axios", lean: -0.2 },
  { name: "Bloomberg", lean: -0.3 },
  { name: "Fox News", lean: 1.4 },
  { name: "The Wall Street Journal Opinion", lean: 1.1 },
  { name: "National Review", lean: 1.5 },
  { name: "The Dispatch", lean: 0.9 },
  { name: "New York Post", lean: 1.2 },
  { name: "The Hill", lean: 0.1 },
  { name: "Newsweek", lean: -0.1 },
  { name: "Al Jazeera", lean: -0.6 },
  { name: "Financial Times", lean: -0.1 },
];

export function publisherLean(name: string): Lean {
  return PUBLISHERS.find((p) => p.name === name)?.lean ?? 0;
}
