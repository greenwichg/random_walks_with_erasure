import { NextResponse } from "next/server";
import { PROFILE } from "@/mock/data";

export async function GET() {
  return NextResponse.json(PROFILE);
}
