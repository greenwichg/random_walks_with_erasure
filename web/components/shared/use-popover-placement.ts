"use client";

import * as React from "react";

export interface PopoverPlacement {
  top?: number;
  bottom?: number;
  left: number;
  width: number;
  /** Budget for the scrollable list inside the panel, after ~76px of input + padding chrome. */
  listMax: number;
}

/**
 * Viewport-clamped placement for a small `fixed` popover anchored to `rootRef`, shared by the
 * country pickers and the searchable filter dropdowns (extracted from CountryPicker so the two
 * cannot drift). An `absolute left-0` panel walks off the right edge of a phone whenever its
 * trigger does not start the row — seen at 390px on the country picker's first test — so the
 * panel is `fixed`, both axes clamp to the viewport with a 16px gutter, and it flips above the
 * trigger when there is more room there than below.
 *
 * Also owns the dismissal contract while open: outside pointer-press and Escape call `onDismiss`
 * (pass a stable callback — an inline closure would re-arm the listeners every render).
 * Returns null until placed; render the panel only when non-null.
 */
export function usePopoverPlacement(
  rootRef: React.RefObject<HTMLElement | null>,
  open: boolean,
  onDismiss: () => void,
): PopoverPlacement | null {
  const [pos, setPos] = React.useState<PopoverPlacement | null>(null);

  const place = React.useCallback(() => {
    const r = rootRef.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.min(320, window.innerWidth - 32);
    const left = Math.min(Math.max(16, r.left), window.innerWidth - 16 - width);
    const below = window.innerHeight - r.bottom - 24;
    const above = r.top - 24;
    const flip = below < 236 && above > below;
    setPos({
      ...(flip ? { bottom: window.innerHeight - r.top + 8 } : { top: r.bottom + 8 }),
      left,
      width,
      listMax: Math.max(120, Math.min(256, (flip ? above : below) - 76)),
    });
  }, [rootRef]);

  React.useEffect(() => {
    if (!open) return;
    place();
    const onPress = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) onDismiss();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    document.addEventListener("pointerdown", onPress);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("pointerdown", onPress);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", place);
    };
  }, [open, place, onDismiss, rootRef]);

  return open ? pos : null;
}
