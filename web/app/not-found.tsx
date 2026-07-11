"use client";

import Link from "next/link";
import { ArrowLeft, Compass } from "lucide-react";
import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

/** Branded 404 — rendered at the root, inside the app-wide Providers (so it localizes). */
export default function NotFound() {
  const { t } = useTranslation();
  return (
    <div className="grid min-h-screen place-items-center px-4">
      <div className="flex max-w-md flex-col items-center text-center">
        <Logo className="mb-8" />
        <p className="text-6xl font-semibold tracking-tight text-primary">{t("notFound.code")}</p>
        <h1 className="mt-3 text-xl font-semibold tracking-tight">{t("notFound.title")}</h1>
        <p className="mt-2 text-sm text-muted-foreground">{t("notFound.body")}</p>
        <div className="mt-6 flex items-center gap-3">
          <Button asChild>
            <Link href="/">
              <ArrowLeft className="h-4 w-4" /> {t("notFound.back")}
            </Link>
          </Button>
          <Button variant="outline" asChild>
            <Link href="/discover">
              <Compass className="h-4 w-4" /> {t("notFound.explore")}
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
