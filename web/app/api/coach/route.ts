import { NextResponse } from "next/server";
import { COACH_GREETING, coachReply } from "@/mock/data";

export async function GET() {
  return NextResponse.json([COACH_GREETING]);
}

/** Send a message → grounded coach reply. Mock stands in for the narrate service. */
export async function POST(request: Request) {
  const { message } = (await request.json().catch(() => ({ message: "" }))) as { message: string };
  return NextResponse.json(coachReply(message ?? ""));
}
