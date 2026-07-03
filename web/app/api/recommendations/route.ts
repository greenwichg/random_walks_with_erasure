import { NextResponse } from "next/server";
import { RECOMMENDATIONS } from "@/mock/data";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const strategy = searchParams.get("strategy");
  const data = strategy ? RECOMMENDATIONS.filter((r) => r.strategy === strategy) : RECOMMENDATIONS;
  return NextResponse.json(data);
}

/** Record feedback (save / ignore / like / …). Mock: echo it back. */
export async function POST(request: Request) {
  const body = await request.json().catch(() => ({}));
  return NextResponse.json({ ok: true, ...body });
}
