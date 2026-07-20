import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "@/components/providers";

export const metadata: Metadata = {
  title: {
    default: "Information Health",
    template: "%s · Information Health",
  },
  description:
    "Understand and balance your news diet. Information Health scores how diverse, calm, and cross-cutting your reading is — and helps you improve it.",
  applicationName: "Information Health",
  authors: [{ name: "Information Health" }],
  keywords: ["news", "media literacy", "recommendations", "echo chamber", "reading diet"],
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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-screen font-sans">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
