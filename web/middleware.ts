import { withAuth } from "next-auth/middleware";

// Gate the authenticated app pages behind Google sign-in. Unauthenticated visitors are sent
// to the public onboarding funnel (value → estimate → sign-in), so they meet the product
// before an account. /onboarding, /signin, /api/*, and static assets are outside the matcher
// below, so they stay public.
export default withAuth({ pages: { signIn: "/onboarding" } });

export const config = {
  matcher: [
    "/",
    "/report/:path*",
    "/recommendations/:path*",
    "/coach/:path*",
    "/history/:path*",
    "/analytics/:path*",
    "/settings/:path*",
    "/profile/:path*",
    "/stories/:path*",
    "/discover/:path*",
  ],
};
