import { PLACEHOLDER_HUES } from "@ih/core/logic/placeholder-art";

/**
 * THE default card art — a stack of folded newspapers, drawn in house, for every story or article
 * card whose own image is missing, suspect or dead.
 *
 * WHY IT IS DRAWN AND NOT A PHOTOGRAPH. The brief supplied a newspaper stock photo, but the file
 * was a watermarked Getty comp: shipping it would put another company's watermark on every
 * imageless card and redistribute an unlicensed asset. So this is a house illustration of the same
 * subject, with the swap point one line away — see {@link FALLBACK_PHOTO_SRC}. Drop a licensed
 * photograph in `web/public/` and point that constant at it, and every surface below picks it up
 * with no other change.
 *
 * WHY INLINE SVG. Three things a raster in `public/` cannot do here: follow the reader's theme (the
 * paper is `--card` and the ink `--foreground`, so the dark theme is not a bright rectangle punched
 * into a charcoal grid), scale from a 72px list thumbnail to a 21/9 hero without a second asset,
 * and cost no network request on a grid of twenty-four cards. It crops exactly like the photo it
 * stands in for: `slice` is the SVG spelling of `object-fit: cover`.
 *
 * COLOUR. The three accent chips come from PLACEHOLDER_HUES, the same curated wheel the publisher
 * plate drew from, for the reason that file states: every hue keeps ≥20° clear of the lean axis
 * (left blue 214 / right red 356). On a product whose whole vocabulary is left-blue and right-red,
 * a big red or blue splash repeated on every imageless card would read as a political claim about
 * the story. The reference photo's red and blue are exactly what must not be copied here.
 *
 * DECORATIVE, ALWAYS. It carries no information and is marked `aria-hidden` by its host slot: it
 * says "no picture was published with this story", and every card already names its publisher,
 * topic and coverage in text beside it. It must never be mistaken for the story's own art, which
 * is why it is unmistakably an illustration rather than a photograph.
 */

/**
 * A licensed photograph to use instead of the drawing — e.g. `"/story-fallback.jpg"` for a file in
 * `web/public/`. `null` (the default) uses the drawing. This is the ONLY line that changes when a
 * licence is in place; the crop, the responsiveness and every call site stay as they are.
 */
export const FALLBACK_PHOTO_SRC: string | null = null;

/** Amber, cyan, violet from the curated wheel — the newsprint colour chips. Never 214/356. */
const [AMBER, CYAN, VIOLET] = [PLACEHOLDER_HUES[1], PLACEHOLDER_HUES[4], PLACEHOLDER_HUES[6]];

/** One folded sheet: x/y offset down the stack, and how much of its face is visible. */
const SHEETS = [
  { y: 96, skew: -13, ink: 0.05, face: 0.55 },
  { y: 132, skew: -13, ink: 0.07, face: 0.7 },
  { y: 168, skew: -13, ink: 0.06, face: 0.85 },
  { y: 204, skew: -13, ink: 0.05, face: 1 },
] as const;

export function StoryFallbackArt({ className }: { className?: string }) {
  if (FALLBACK_PHOTO_SRC) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={FALLBACK_PHOTO_SRC}
        alt=""
        loading="lazy"
        decoding="async"
        className={className ?? "h-full w-full object-cover"}
      />
    );
  }
  return (
    <svg
      viewBox="0 0 400 300"
      preserveAspectRatio="xMidYMid slice"
      className={className ?? "h-full w-full"}
      role="presentation"
      focusable="false"
    >
      <defs>
        <linearGradient id="hv-fb-ground" x1="0" y1="0" x2="0.3" y2="1">
          <stop offset="0%" stopColor="hsl(var(--muted))" />
          <stop offset="100%" stopColor="hsl(var(--accent))" />
        </linearGradient>
        {/* The out-of-focus far page: the reference photo's shallow depth of field, as a blur. */}
        <filter id="hv-fb-blur" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
      </defs>

      <rect width="400" height="300" fill="url(#hv-fb-ground)" />

      {/* Far, defocused newsprint — colour blocks reading as a page that is out of the plane. */}
      <g filter="url(#hv-fb-blur)" opacity="0.5">
        <rect x="-10" y="-6" width="420" height="104" fill="hsl(var(--card))" />
        <rect x="16" y="10" width="128" height="58" rx="6" fill={`hsl(${CYAN} 62% 52% / 0.5)`} />
        <rect x="166" y="4" width="96" height="40" rx="6" fill={`hsl(${AMBER} 78% 55% / 0.45)`} />
        <rect x="150" y="52" width="220" height="34" rx="6" fill={`hsl(${VIOLET} 50% 58% / 0.4)`} />
      </g>

      {/* The stack: folded sheets running lower-left to upper-right, each lifted off the last. */}
      {SHEETS.map((sheet, i) => (
        <g key={i} transform={`rotate(${sheet.skew} 200 ${sheet.y}) translate(0 ${sheet.y})`}>
          {/* The shadow the sheet above casts into this one's fold. */}
          <rect x="-60" y="-9" width="520" height="12" fill={`hsl(var(--foreground) / ${sheet.ink})`} />
          <rect x="-60" y="0" width="520" height="34" fill="hsl(var(--card))" />
          {/* The fold's own crease, a hairline along the spine. */}
          <rect x="-60" y="32" width="520" height="2" fill="hsl(var(--foreground) / 0.08)" />

          {/* Headline type on the visible face — heavy bars, deliberately unreadable letterforms:
              the art must not appear to quote a headline this story does not have. */}
          <g opacity={sheet.face} fill="hsl(var(--foreground) / 0.72)">
            <rect x="24" y="8" width={54 + i * 22} height="15" rx="2" />
            <rect x={86 + i * 22} y="8" width={34 + i * 8} height="15" rx="2" />
            {i > 1 && <rect x={128 + i * 30} y="8" width="46" height="15" rx="2" />}
          </g>
          {/* Column rules — the body text beneath the masthead, at newsprint weight. */}
          <g opacity={sheet.face * 0.5} fill="hsl(var(--foreground) / 0.28)">
            {[0, 1, 2, 3, 4].map((c) => (
              <rect key={c} x={236 + c * 30} y="10" width="18" height="11" rx="1" />
            ))}
          </g>
        </g>
      ))}

      {/* The colour chips down the right edge — a sports or listings section in the stack. */}
      <g transform="rotate(-13 200 150)">
        <rect x="330" y="118" width="22" height="16" rx="3" fill={`hsl(${CYAN} 60% 48%)`} opacity="0.85" />
        <rect x="356" y="118" width="22" height="16" rx="3" fill={`hsl(${AMBER} 80% 55%)`} opacity="0.85" />
        <rect x="330" y="154" width="22" height="16" rx="3" fill={`hsl(${VIOLET} 48% 55%)`} opacity="0.8" />
      </g>

      {/* A held-back vignette so the stack sits in light rather than floating on flat colour. */}
      <rect width="400" height="300" fill="hsl(var(--foreground) / 0.03)" />
    </svg>
  );
}
