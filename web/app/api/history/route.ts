import { NextResponse } from "next/server";
import { HISTORY } from "@/mock/data";

export async function GET() {
  return NextResponse.json(HISTORY);
}
