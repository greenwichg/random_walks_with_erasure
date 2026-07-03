import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { InfoTooltip } from "@/components/shared/info-tooltip";
import { cn } from "@/lib/utils";

/** A titled card with optional info tooltip + header action — used across pages. */
export function SectionCard({
  title,
  info,
  action,
  children,
  className,
  contentClassName,
}: {
  title: string;
  info?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <Card className={className}>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-1.5">
          <h3 className="text-sm font-semibold">{title}</h3>
          {info && <InfoTooltip text={info} />}
        </div>
        {action}
      </CardHeader>
      <CardContent className={cn("pt-0", contentClassName)}>{children}</CardContent>
    </Card>
  );
}
