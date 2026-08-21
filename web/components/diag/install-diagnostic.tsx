"use client";

import * as React from "react";
import {
  installOffer,
  isIosSafari,
  isIosInAppBrowser,
} from "@/lib/install-prompt";

/**
 * TEMPORARY — delete with `app/diag/install/page.tsx`.
 *
 * Reports what the browser says about itself, and what the CURRENT production detector concludes
 * from it. It imports `lib/install-prompt` rather than reimplementing any of it, so what shows on
 * screen is what the live banner decides — a copy of the logic here could agree with itself while
 * disagreeing with production, which would make the measurement worthless.
 *
 * Nothing here is a fix. No production behaviour changes because this file exists.
 */

/** One probe: what it measures, and why it might separate Safari from an embedded WKWebView. */
interface Probe {
  label: string;
  value: string;
  /** Shown small under the value — what a Safari answer vs a WKWebView answer is expected to be. */
  note?: string;
}

function read(): { ua: string; probes: Probe[]; verdict: Probe[] } {
  const nav = navigator as Navigator & {
    standalone?: boolean;
    vendor?: string;
  };
  const ua = navigator.userAgent;

  // `window.webkit.messageHandlers` is how a native host talks to its WKWebView. Safari has no
  // native host, so it has no reason to define it.
  const wk = (window as { webkit?: { messageHandlers?: unknown } }).webkit;

  const probes: Probe[] = [
    {
      label: "typeof navigator.standalone",
      value: typeof nav.standalone,
      note: 'Mobile Safari defines it ("boolean"); a plain WKWebView leaves it "undefined".',
    },
    {
      label: "navigator.standalone",
      value: String(nav.standalone),
      note: "true only when launched from the home screen.",
    },
    {
      label: "window.webkit?.messageHandlers",
      value: wk?.messageHandlers === undefined ? "undefined" : "present",
      note: "Present when a native app hosts this web view.",
    },
    {
      label: "'serviceWorker' in navigator",
      value: String("serviceWorker" in navigator),
      note: "Historically Safari-only on iOS; many in-app web views lack it.",
    },
    {
      label: "serviceWorker.controller",
      value:
        "serviceWorker" in navigator
          ? navigator.serviceWorker.controller
            ? "controlling"
            : "none"
          : "n/a",
    },
    {
      label: "display-mode: standalone",
      value: String(
        window.matchMedia?.("(display-mode: standalone)").matches ?? "n/a",
      ),
    },
    { label: "navigator.maxTouchPoints", value: String(navigator.maxTouchPoints) },
    { label: "navigator.vendor", value: nav.vendor || "(empty)" },
    {
      label: "window.innerHeight / screen.height",
      value: `${window.innerHeight} / ${window.screen.height}`,
      note: "Browser chrome differs between Safari and an in-app view.",
    },
    { label: "document.referrer", value: document.referrer || "(empty)" },
  ];

  // What the LIVE detector concludes. These three are the whole point of the exercise.
  const inApp = isIosInAppBrowser(ua, navigator.maxTouchPoints);
  const safari = isIosSafari(ua, navigator.maxTouchPoints);
  const verdict: Probe[] = [
    { label: "isIosInAppBrowser()", value: String(inApp) },
    { label: "isIosSafari()", value: String(safari) },
    {
      label: "installOffer()",
      value: installOffer({
        installed: false,
        // WebKit never fires beforeinstallprompt, so this is the honest input on iOS. Hard-coded
        // rather than listened for: the page would otherwise report a different answer depending
        // on how long it had been open.
        nativePromptReady: false,
        iosSafari: safari,
        iosInAppBrowser: inApp,
        dismissedAt: null,
        now: Date.now(),
      }),
    },
  ];
  return { ua, probes, verdict };
}

export function InstallDiagnostic() {
  const [state, setState] = React.useState<ReturnType<typeof read> | null>(null);
  const [copied, setCopied] = React.useState(false);

  // In an effect, not during render: every value here comes from `navigator` / `window`, which do
  // not exist while the server renders this.
  React.useEffect(() => setState(read()), []);

  const asText = state
    ? [
        `UA: ${state.ua}`,
        ...state.probes.map((p) => `${p.label}: ${p.value}`),
        ...state.verdict.map((p) => `${p.label}: ${p.value}`),
      ].join("\n")
    : "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(asText);
      setCopied(true);
    } catch {
      // Clipboard access is restricted in some embedded web views — which is exactly the kind of
      // browser this page exists to measure. The <pre> below is selectable either way.
      setCopied(false);
    }
  };

  if (!state) return null;

  return (
    <main className="mx-auto max-w-2xl px-4 py-8 font-mono text-[13px] leading-relaxed">
      <h1 className="text-base font-bold">Install diagnostic (temporary)</h1>
      <p className="mt-1 text-xs text-muted-foreground">
        Screenshot this, or tap Copy and paste it back.
      </p>

      <Section title="User agent">
        <p className="break-all rounded-lg border bg-muted/30 p-3">{state.ua}</p>
      </Section>

      <Section title="What the live detector concludes">
        <ul className="rounded-lg border bg-muted/30 p-3">
          {state.verdict.map((p) => (
            <li key={p.label} className="flex flex-wrap justify-between gap-x-4">
              <span className="text-muted-foreground">{p.label}</span>
              <span className="font-bold text-primary">{p.value}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="Probes">
        <ul className="space-y-2.5 rounded-lg border bg-muted/30 p-3">
          {state.probes.map((p) => (
            <li key={p.label}>
              <div className="flex flex-wrap justify-between gap-x-4">
                <span className="text-muted-foreground">{p.label}</span>
                <span className="break-all font-bold">{p.value}</span>
              </div>
              {p.note && (
                <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground/70">
                  {p.note}
                </p>
              )}
            </li>
          ))}
        </ul>
      </Section>

      <button
        onClick={copy}
        className="mt-5 w-full rounded-lg bg-primary px-4 py-2.5 font-sans text-sm font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {copied ? "Copied" : "Copy all"}
      </button>

      {/* The fallback for a web view that blocks the clipboard API: long-press to select. */}
      <pre className="mt-4 select-all whitespace-pre-wrap break-all rounded-lg border bg-muted/30 p-3 text-[11px] text-muted-foreground">
        {asText}
      </pre>
    </main>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-5">
      <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}
