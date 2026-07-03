import { NextResponse } from "next/server";
import { STORIES } from "@/mock/data";

export async function GET(_req: Request, { params }: { params: { id: string } }) {
  const story = STORIES.find((s) => s.id === params.id);
  if (!story) return NextResponse.json({ message: "Story not found" }, { status: 404 });
  return NextResponse.json(story);
}
