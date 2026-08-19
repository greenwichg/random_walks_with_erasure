"use client";

/**
 * A compact horizontal share bar with an optional dotted legend — shared by the Information Health
 * strip's Political Balance tile and the Daily Summary's political/emotional exposure. Colors are
 * passed as CSS colors (inline styles) because a segment's hue comes from data, not from a fixed
 * class the bundler could see. Segments with a zero value are omitted.
 */
export interface Segment {
  key: string;
  label: string;
  value: number;
  color: string;
}

export function DistributionBar({
  segments,
  legend = true,
  showLabels = true,
}: {
  segments: Segment[];
  legend?: boolean;
  /** Show each segment's label in the legend (else just a colour dot + %). */
  showLabels?: boolean;
}) {
  const total = segments.reduce((s, x) => s + x.value, 0) || 1;
  const shown = segments.filter((s) => s.value > 0);
  return (
    <div>
      <div className="flex h-2 overflow-hidden rounded-full bg-muted" role="presentation">
        {shown.map((s) => (
          <span key={s.key} style={{ width: `${(s.value / total) * 100}%`, backgroundColor: s.color }} />
        ))}
      </div>
      {legend && (
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.7rem] tabular-nums text-muted-foreground">
          {shown.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: s.color }} />
              {showLabels && `${s.label} `}
              {Math.round((s.value / total) * 100)}%
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
