"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import type { Metric } from "@/types/domain";
import { METRIC_ORDER, METRICS } from "@/lib/metrics";
import { DeltaBadge } from "@/components/shared/delta-badge";
import { cn } from "@/lib/utils";

const HUE_BG: Record<string, string> = {
  primary: "bg-primary/10 text-primary",
  left: "bg-left/12 text-left",
  center: "bg-center/12 text-center",
  right: "bg-right/12 text-right",
  positive: "bg-positive/12 text-positive",
  caution: "bg-caution/15 text-caution",
};
const HUE_BAR: Record<string, string> = {
  primary: "bg-primary",
  left: "bg-left",
  center: "bg-center",
  right: "bg-right",
  positive: "bg-positive",
  caution: "bg-caution",
};

/** Expandable per-metric rows — the report's detailed breakdown. */
export function MetricAccordion({ metrics }: { metrics: Metric[] }) {
  const [open, setOpen] = React.useState<string | null>(null);

  return (
    <div className="divide-y rounded-lg border bg-card">
      {METRIC_ORDER.map((key) => {
        const metric = metrics.find((m) => m.key === key);
        if (!metric) return null;
        const meta = METRICS[key];
        const Icon = meta.icon;
        const isOpen = open === key;
        return (
          <div key={key} id={key} className="scroll-mt-24">
            <button
              onClick={() => setOpen(isOpen ? null : key)}
              className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-accent/40"
              aria-expanded={isOpen}
            >
              <span className={cn("grid h-9 w-9 shrink-0 place-items-center rounded-lg", HUE_BG[meta.hue])}>
                <Icon className="h-[1.1rem] w-[1.1rem]" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{meta.label}</span>
                  <DeltaBadge value={metric.delta} />
                </div>
                <div className="mt-1.5 h-1.5 w-full max-w-sm overflow-hidden rounded-full bg-muted">
                  <div
                    className={cn("h-full rounded-full", HUE_BAR[meta.hue])}
                    style={{ width: `${Math.max(0, Math.min(100, metric.score))}%` }}
                  />
                </div>
              </div>
              <span className="shrink-0 text-lg font-semibold tabular-nums">{Math.round(metric.score)}</span>
              <ChevronDown
                className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", isOpen && "rotate-180")}
              />
            </button>
            <AnimatePresence initial={false}>
              {isOpen && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                  className="overflow-hidden"
                >
                  <div className="px-4 pb-4 pl-16 text-sm text-muted-foreground">
                    <p>{meta.description}</p>
                    <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs">
                      {metric.raw && (
                        <span>
                          Your value:{" "}
                          <span className="font-medium text-foreground">
                            {metric.raw.value} {metric.raw.unit}
                          </span>
                        </span>
                      )}
                      {typeof metric.benchmark === "number" && (
                        <span>
                          Typical reader:{" "}
                          <span className="font-medium text-foreground">{metric.benchmark}</span>
                        </span>
                      )}
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </div>
  );
}
