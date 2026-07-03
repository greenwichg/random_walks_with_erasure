import { NextResponse } from "next/server";
import { ARTICLES, STORIES } from "@/mock/data";

/** Unified search across articles + stories. Mock: case-insensitive contains. */
export async function GET(request: Request) {
  const q = (new URL(request.url).searchParams.get("q") ?? "").toLowerCase().trim();
  if (!q) return NextResponse.json({ articles: [], stories: [] });
  const articles = ARTICLES.filter(
    (a) => a.headline.toLowerCase().includes(q) || a.publisher.toLowerCase().includes(q) || a.topic.toLowerCase().includes(q),
  ).slice(0, 8);
  const stories = STORIES.filter(
    (s) => s.title.toLowerCase().includes(q) || s.topic.toLowerCase().includes(q),
  ).slice(0, 5);
  return NextResponse.json({ articles, stories });
}
