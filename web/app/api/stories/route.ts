import { NextResponse } from "next/server";
import { STORIES } from "@/mock/data";

export async function GET() {
  return NextResponse.json(STORIES);
}
