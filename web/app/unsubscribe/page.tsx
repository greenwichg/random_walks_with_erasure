"use client";

import * as React from "react";
import Link from "next/link";
import { CheckCircle2, Mail } from "lucide-react";
import { useTranslation } from "@/lib/i18n";

/**
 * The page an unsubscribe link in an email lands on.
 *
 * **Outside `(app)`, and therefore outside the auth gate, on purpose.** A reader clicking
 * unsubscribe is in a mail client, possibly on a device they have never signed in on, possibly
 * years later. Asking them to log in first is what gets mail reported as spam instead of
 * unsubscribed — so the signed token in the link is the whole authorisation, and it authorises
 * exactly one thing.
 *
 * It acts on arrival rather than showing a confirm button. A one-click unsubscribe is what the
 * List-Unsubscribe-Post header promises the mail client (RFC 8058), and a reader who has already
 * decided should not be asked to decide again.
 */
export default function UnsubscribePage() {
  const { t } = useTranslation();
  const [state, setState] = React.useState<"working" | "done" | "failed">("working");

  React.useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("t") ?? "";
    if (!token) {
      setState("failed");
      return;
    }
    let alive = true;
    fetch("/api/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    })
      .then((r) => r.json())
      .then((d) => alive && setState(d?.unsubscribed ? "done" : "failed"))
      .catch(() => alive && setState("failed"));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-16">
      <div className="rounded-2xl border bg-card p-8 text-center shadow-soft">
        <span className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-primary/10 text-primary">
          {state === "done" ? <CheckCircle2 className="h-6 w-6" /> : <Mail className="h-6 w-6" />}
        </span>

        {state === "working" && <p className="text-sm text-muted-foreground">{t("unsubscribe.working")}</p>}

        {state === "done" && (
          <>
            <h1 className="text-lg font-semibold tracking-tight">{t("unsubscribe.title")}</h1>
            <p className="mt-2 text-sm text-muted-foreground">{t("unsubscribe.done")}</p>
            {/* Said plainly, because it is the thing a reader is most likely to get wrong: the
                digest itself has not been deleted, only the email copy of it. */}
            <p className="mt-3 text-sm text-muted-foreground">{t("unsubscribe.stillHere")}</p>
          </>
        )}

        {state === "failed" && (
          <>
            <h1 className="text-lg font-semibold tracking-tight">{t("unsubscribe.failed")}</h1>
            <p className="mt-2 text-sm text-muted-foreground">{t("unsubscribe.failedBody")}</p>
          </>
        )}

        {state !== "working" && (
          <Link
            href="/settings"
            className="mt-6 inline-block rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground"
          >
            {t("unsubscribe.settings")}
          </Link>
        )}
      </div>
    </main>
  );
}
