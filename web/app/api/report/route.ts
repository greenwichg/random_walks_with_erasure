import { NextResponse } from "next/server";
import { REPORT } from "@/mock/data";

export async function GET() {
  return NextResponse.json(REPORT);
}
