import { withAuth } from "next-auth/middleware";

// Gate the authenticated app pages behind Google sign-in; unauthenticated visitors are
// redirected to /signin. Public routes (/signin, /api/*, static assets) are left out of
// the matcher. Milestone B adds the public onboarding/landing surface and widens this.
export default withAuth({ pages: { signIn: "/signin" } });

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
