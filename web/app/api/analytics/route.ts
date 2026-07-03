import { NextResponse } from "next/server";
import { ANALYTICS } from "@/mock/data";

export async function GET() {
  return NextResponse.json(ANALYTICS);
}
