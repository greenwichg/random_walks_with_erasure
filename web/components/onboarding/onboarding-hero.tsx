"use client";

import { motion } from "framer-motion";
import { Scale, Search, ShieldCheck, Zap } from "lucide-react";
import { useTranslation } from "@/lib/i18n";

/**
 * The public pitch beside the onboarding card — the left half of the split hero.
 *
 * This exists because the funnel's first screen was a single centered card: a form with no
 * argument in front of it. A visitor arriving cold saw the ask ("Build my report") before the
 * reason, and the product's actual position — that it hands judgement back to the reader rather
 * than making it for them — appeared nowhere. So the left column carries the claim and the
 * philosophy, and the card keeps the action, unchanged.
 *
 * Rendered ONLY on the welcome step (see `OnboardingFlow`): once the reader commits, the pitch
 * has done its job and every later step narrows to the card alone. The headline is the page's
 * `h1` — the card's title steps down to `h2` while this is on screen, so the heading order stays
 * honest for a screen reader rather than announcing two competing first-level headings.
 *
 * The accent halves (`hero.bodyAccent`, `philosophyAccent`) are separate keys rather than markup
 * embedded in one string: a translator needs to place the emphasis where their own grammar puts
 * it, which a sentence with a hard-coded `<span>` in the middle cannot express.
 */
export function OnboardingHero() {
  const { t } = useTranslation();

  const features = [
    {
      Icon: ShieldCheck,
      title: t("onboarding.feature1.t"),
      body: t("onboarding.feature1.d"),
    },
    {
      Icon: Search,
      title: t("onboarding.feature2.t"),
      body: t("onboarding.feature2.d"),
    },
    {
      Icon: Scale,
      title: t("onboarding.feature3.t"),
      body: t("onboarding.feature3.d"),
    },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="w-full lg:flex-1"
    >
      <h1 className="max-w-[15ch] text-balance text-4xl font-bold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl">
        {t("onboarding.hero.title")}
      </h1>

      <p className="mt-6 max-w-xl text-pretty leading-relaxed text-muted-foreground sm:text-lg">
        {t("onboarding.hero.body")}{" "}
        <span className="text-primary">{t("onboarding.hero.bodyAccent")}</span>
      </p>

      <hr className="my-8 max-w-xl border-border/60" />

      {/* Rows on a phone, centred columns from `sm` up. Three stacked centre-aligned blocks read
          as three separate announcements and pushed the actual CTA a screen and a half down; as
          rows they scan in one pass and the card stays reachable. */}
      {/* Plain `sm:text-center` is safe again: the lean colours were renamed `lean-*`, so the
          alignment utilities no longer collide with theme colour names (tailwind.config.ts). This
          used to be `sm:[text-align:center]` to dodge that shadowing. */}
      <ul className="grid max-w-xl gap-5 sm:grid-cols-3 sm:gap-6">
        {features.map(({ Icon, title, body }) => (
          <li
            key={title}
            className="flex items-start gap-3 sm:block sm:text-center"
          >
            <span className="grid h-11 w-11 flex-none place-items-center rounded-2xl border border-primary/20 bg-primary/10 text-primary sm:mx-auto">
              <Icon className="h-5 w-5" aria-hidden />
            </span>
            <div className="sm:mt-3">
              <span className="block text-sm font-semibold tracking-tight text-foreground">
                {title}
              </span>
              <span className="mt-1 block text-pretty text-xs leading-relaxed text-muted-foreground">
                {body}
              </span>
            </div>
          </li>
        ))}
      </ul>

      {/* The position statement. Given its own surface rather than another paragraph: it is the
          one line on this page that is an argument rather than a description. */}
      <div className="mt-10 flex max-w-xl items-center gap-3 rounded-2xl border bg-card/60 p-4 backdrop-blur-sm">
        <span className="grid h-9 w-9 flex-none place-items-center rounded-xl bg-primary/10 text-primary">
          <Zap className="h-4 w-4" aria-hidden />
        </span>
        <p className="text-pretty text-sm leading-relaxed">
          {t("onboarding.philosophy")}{" "}
          <span className="font-medium text-primary">
            {t("onboarding.philosophyAccent")}
          </span>
        </p>
      </div>
    </motion.div>
  );
}
