"use client";

import * as React from "react";
import { AlertTriangle, RefreshCw, Home } from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { reportError } from "@/lib/observability";

/** App-level error boundary — catches render/runtime errors in any page. */
export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  React.useEffect(() => {
    // Report through the vendor-agnostic reporter (console in dev, beacon → backend in prod).
    reportError(error, { digest: error.digest });
  }, [error]);
  const { t } = useTranslation();

  return (
    <div className="grid min-h-[60vh] place-items-center px-4">
      <div className="flex max-w-md flex-col items-center text-center">
        <div className="mb-5 grid h-14 w-14 place-items-center rounded-2xl bg-destructive/10 text-destructive">
          <AlertTriangle className="h-7 w-7" />
        </div>
        <h1 className="text-xl font-semibold tracking-tight">{t("error.title")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("error.body")}</p>
        <div className="mt-6 flex items-center gap-3">
          <Button onClick={reset}>
            <RefreshCw className="h-4 w-4" /> {t("common.tryAgain")}
          </Button>
          <Button variant="outline" asChild>
            <Link href="/">
              <Home className="h-4 w-4" /> {t("common.dashboard")}
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
