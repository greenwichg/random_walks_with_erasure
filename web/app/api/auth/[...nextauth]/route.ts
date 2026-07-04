import NextAuth from "next-auth";
import { authOptions } from "@/lib/auth";

// The NextAuth request handler for every /api/auth/* route (sign-in, callback, session…).
const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };
