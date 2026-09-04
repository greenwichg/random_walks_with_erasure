"use client";

import * as React from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A collapsible story section — the phone's whole story page is a stack of these.
 *
 * WHY A PHONE READS BETTER THIS WAY. The story page carries six modules, each answering a
 * different question, and on a phone they became one long scroll in which everything was equally
 * present and therefore nothing was findable: a reader after the publisher list scrolled past the
 * timeline, the three breakdown tabs and the framing comparison to reach it. Collapsed, the same
 * six are one screen of TITLES — the page becomes a table of contents for itself, and opening one
 * is a decision rather than a scroll.
 *
 * THE DESCRIPTION IS THE POINT, not decoration. A bare list of titles asks the reader to guess
 * what "Breakdown" contains before spending a tap on it; the line under each title answers that,
 * so a collapsed section still says what it holds. It therefore stays visible when the section is
 * open too — it reads as a standfirst there, and a line that vanished on expand would flicker the
 * whole stack every time one opened.
 *
 * The header IS the control (the accordion pattern): a button inside the h2, so assistive tech
 * announces the section by name and its expanded state in one stop, and `aria-controls` points at
 * the region the press reveals.
 *
 * DESKTOP DOES NOT USE THIS. There the same modules sit in two columns with room for all of them
 * at once, so collapsing would hide content the layout has already found space for. The story page
 * mounts one composition or the other (`useIsDesktop`), and this component is only in the mobile
 * one.
 */
export function StorySection({
  id,
  title,
  description,
  defaultOpen = false,
  children,
}: {
  /** Stable id — the heading's, and the base for the region's. */
  id: string;
  title: string;
  /** One line saying what is inside. Visible in both states. */
  description: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  // A reader who has asked for less motion gets the same reveal with no travel — the section still
  // opens, it simply does not animate to get there.
  const still = useReducedMotion();

  return (
    // Edge-to-edge on a phone: the negative margin cancels the page gutter so each panel spans the
    // full width like the reference, while its own padding keeps the text on the same measure as
    // every other section. `bg-card` over the page ground is what makes the gap between panels
    // read as a divider without a rule to draw.
    <section aria-labelledby={`${id}-heading`} className="-mx-4 bg-card px-4">
      <h2 id={`${id}-heading`}>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={`${id}-region`}
          onClick={() => setOpen((v) => !v)}
          className={cn(
            "flex w-full items-center justify-between gap-4 py-5 text-left",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
          )}
        >
          <span className="text-[26px] font-bold leading-tight tracking-tight">{title}</span>
          <ChevronDown
            className={cn(
              "h-6 w-6 shrink-0 text-muted-foreground transition-transform duration-200",
              open && "rotate-180",
            )}
            aria-hidden
          />
        </button>
      </h2>

      {/* Pulled up under the title: the button's own padding already spaces the two, and the
          description belongs to the heading rather than to the content it introduces. */}
      <p className="-mt-2 pb-5 text-[15px] leading-snug text-muted-foreground">{description}</p>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={`${id}-region`}
            role="region"
            aria-labelledby={`${id}-heading`}
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={still ? { duration: 0 } : { duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
            // Only the height needs clipping while it travels. Menus and tooltips inside these
            // modules are portalled (Radix), so nothing that must escape the box lives in it.
            style={{ overflow: "hidden" }}
          >
            <div className="pb-6">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

/**
 * The stack itself. `gap-2` over the page ground is the band between panels in the reference —
 * the panels carry the surface, the gaps show the page through, and no border is drawn at all.
 */
export function StorySections({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-col gap-2">{children}</div>;
}
