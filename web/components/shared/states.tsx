import { AlertCircle, Inbox, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";

/** Reusable empty state. */
export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ElementType;
  title?: string;
  description?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed bg-card/40 px-6 py-16 text-center",
        className,
      )}
    >
      <div className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-muted text-muted-foreground">
        <Icon className="h-6 w-6" />
      </div>
      <p className="font-medium">{title ?? t("states.empty.title")}</p>
      {description && <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}

/**
 * In-card placeholder for a chart with nothing to plot.
 *
 * Deliberately quieter than `EmptyState`: this sits INSIDE a `SectionCard` that already carries the
 * title and the explanatory tooltip, so a second bordered panel with its own icon and heading would
 * be a box inside a box saying the same thing twice. It reserves the chart's own height for the
 * same reason the chart wrappers do — a placeholder that collapses would make a grid of cards jump
 * as data arrives.
 */
export function ChartEmpty({ height, className }: { height?: number; className?: string }) {
  const { t } = useTranslation();
  return (
    <div
      className={cn("flex items-center justify-center text-sm text-muted-foreground", className)}
      style={{ minHeight: height }}
    >
      {t("states.chartEmpty")}
    </div>
  );
}

/** Reusable error state with a retry action. */
export function ErrorState({
  message,
  onRetry,
  className,
}: {
  message?: string;
  onRetry?: () => void;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-destructive/20 bg-destructive/[0.03] px-6 py-16 text-center",
        className,
      )}
    >
      <div className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-destructive/10 text-destructive">
        <AlertCircle className="h-6 w-6" />
      </div>
      <p className="font-medium">{t("states.error.title")}</p>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{message ?? t("states.error.body")}</p>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-5" onClick={onRetry}>
          <RefreshCw className="h-4 w-4" /> {t("common.tryAgain")}
        </Button>
      )}
    </div>
  );
}
