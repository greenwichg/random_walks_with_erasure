"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { Eye, TrendingUp, Plus, Check } from "lucide-react";
import type { BlindSpot, Improvement } from "@/types/domain";
import { METRICS } from "@/lib/metrics";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** Blind spots — under-consumed topics/sides. */
import { useTranslation } from "@/lib/i18n";

export function BlindSpots({ items }: { items: BlindSpot[] }) {
  return (
    <div className="space-y-3">
      {items.map((b, i) => (
        <motion.div
          key={b.topic}
          initial={{ opacity: 0, x: -6 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: i * 0.06 }}
          className="flex items-start gap-3 rounded-lg border bg-card/60 p-3"
        >
          <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-caution/12 text-caution">
            <Eye className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="font-medium">{b.topic}</span>
              <Badge variant="caution">{Math.round(b.gap * 100)}% gap</Badge>
            </div>
            <p className="mt-0.5 text-sm text-muted-foreground">{b.note}</p>
          </div>
        </motion.div>
      ))}
    </div>
  );
}

/** Improvement recommendations — interactive: add to weekly goals. */
const ACCEPTED_STATES = new Set(["accepted", "in_progress", "completed"]);

export function Improvements({ items: allItems }: { items: Improvement[] }) {
  const { t } = useTranslation();
  // RC2.4 — the backend feedback-aware ranker already ordered these; render only the visible ones
  // (a suppressed rec carries ranking.visible === false with a reason). Backward compatible: a payload
  // without `ranking` keeps every rec.
  const items = allItems.filter((i) => i.ranking?.visible !== false);
  // RC2.3 — seed the "added to goals" state from the persisted lifecycle so an accepted recommendation
  // stays reflected across reloads (the acceptance now lives in the ledger, not just local state).
  const [added, setAdded] = React.useState<Set<string>>(
    () => new Set(items.filter((i) => i.lifecycle && ACCEPTED_STATES.has(i.lifecycle.state)).map((i) => i.id)),
  );
  const toggle = (id: string) =>
    setAdded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <div className="space-y-3">
      {items.map((imp, i) => {
        const meta = METRICS[imp.metric];
        const isAdded = added.has(imp.id);
        return (
          <motion.div
            key={imp.id}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.06 }}
            className="rounded-lg border bg-card/60 p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium">{imp.title}</p>
                {imp.trigger ? (
                  // RC2.1 — user-specific evidence bound from the report. Falls back to `detail` below
                  // whenever the backend couldn't ground a specific claim (older payloads / thin data).
                  <div className="mt-1 space-y-1 text-sm text-muted-foreground">
                    <p>{imp.trigger}</p>
                    {imp.evidence ? <p>{imp.evidence}</p> : null}
                    {imp.suggestedAction ? (
                      <p className="font-medium text-foreground">{imp.suggestedAction}</p>
                    ) : null}
                  </div>
                ) : (
                  <p className="mt-1 text-sm text-muted-foreground">{imp.detail}</p>
                )}
              </div>
              <Badge
                variant="positive"
                className="shrink-0"
                title={imp.impactEstimate?.explanation}
              >
                <TrendingUp className="h-3 w-3" />{" "}
                {imp.impactEstimate && imp.impactEstimate.low !== imp.impactEstimate.high
                  ? `+${imp.impactEstimate.low}–${imp.impactEstimate.high}`
                  : `+${imp.impactEstimate?.high ?? imp.impact}`}
              </Badge>
            </div>
            <div className="mt-3 flex items-center justify-between">
              <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                <meta.icon className="h-3.5 w-3.5" /> {t("rec.helps", { metric: t(`metric.${meta.key}.label`) })}
              </span>
              <Button
                size="sm"
                variant={isAdded ? "secondary" : "outline"}
                onClick={() => toggle(imp.id)}
                className={cn(isAdded && "text-positive")}
              >
                {isAdded ? (
                  <>
                    <Check className="h-4 w-4" /> Added to goals
                  </>
                ) : (
                  <>
                    <Plus className="h-4 w-4" /> Add to goals
                  </>
                )}
              </Button>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}
