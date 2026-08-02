import type { Metadata, Viewport } from "next";
import { getServerSession } from "next-auth";
import "./globals.css";
import { authOptions } from "@/lib/auth";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: {
    default: "Hidden View",
    template: "%s · Hidden View",
  },
  description:
    "Understand and balance your news diet. Hidden View scores how diverse, calm, and cross-cutting your reading is — and helps you improve it.",
  applicationName: "Hidden View",
  authors: [{ name: "Hidden View" }],
  keywords: ["news", "media literacy", "recommendations", "echo chamber", "reading diet"],
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/icon.svg", type: "image/svg+xml" },
      { url: "/favicon-16x16.png", type: "image/png", sizes: "16x16" },
      { url: "/favicon-32x32.png", type: "image/png", sizes: "32x32" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  manifest: "/site.webmanifest",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0d1117" },
  ],
  width: "device-width",
  initialScale: 1,
  // MB1 H2: expose the device safe-area insets to `env(safe-area-inset-*)` so the sticky header,
  // the mobile drawer, and the settings save bar can clear the notch / home indicator.
  viewportFit: "cover",
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // R1a: resolve the session ON THE SERVER and hand it to the client provider, so the browser does
  // not spend its first round trip asking `/api/auth/session` a question this render already
  // answered. Measured before this existed (RUM harness, 1x CPU): the session request sat at
  // ~337 ms in every hard load's waterfall, and the shell queries gated on its answer fired at
  // ~370-430 ms — one full serial phase, on every page, bought by one `await` here that costs the
  // server ~1-3 ms of JWT decode. Public pages resolve to null, which is exactly what their
  // anonymous session state was anyway.
  const session = await getServerSession(authOptions);
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen font-sans">
        <Providers session={session}>{children}</Providers>
      </body>
    </html>
  );
}
