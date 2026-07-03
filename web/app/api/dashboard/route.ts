import { NextResponse } from "next/server";
import { DASHBOARD } from "@/mock/data";

export async function GET() {
  return NextResponse.json(DASHBOARD);
}
