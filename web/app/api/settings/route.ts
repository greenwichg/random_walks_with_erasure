import { NextResponse } from "next/server";
import { SETTINGS } from "@/mock/data";

export async function GET() {
  return NextResponse.json(SETTINGS);
}
