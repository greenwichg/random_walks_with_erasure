"use client";

import * as React from "react";
import { ArrowLeftRight, ArrowRight, X } from "lucide-react";
import Link from "next/link";
import type { Continuation } from "@/types/domain";
import {
  clearArmed,
  dismissStory,
  mayShow,
  readArmed,
  readState,
  recordImpression,
  subscribeArmed,
} from "@/lib/continuation";
import { useVisibilityReturn } from "@/hooks/use-visibility-return";
import { useRecordRead } from "@/hooks/use-data";
import { track } from "@/lib/analytics";
import { useTranslation } from "@/lib/i18n";

/** Design §2.1: the hidden duration that separates "went and read something" from "looked away". */
const MIN_HIDDEN_MS = 20_000;
/** Design §2.1: reads older than this are past the moment the offer makes sense. */
const FRESHNESS_MS = 4 * 60 * 60 * 1000;

/**
 * "Compare this story" — the offer a reader gets when they come BACK from an article
 * (docs/STORY_CONTINUATION_DESIGN.md §1).
 *
 * Mounted by a card, keyed to that card's article URL. It renders **nothing** unless all of these
 * hold: the reader opened THIS article, the engine found an opposing account, they were away for at
 * least 20 s, the read is under 4 h old, and they have neither dismissed this story nor already
 * seen it twice. Nothing is the overwhelmingly common answer, and it is a real answer — no
 * placeholder, no "no comparison available", no spinner.
 *
 * The copy follows §1.3's four rules, and every one of them is load-bearing:
 *
 *  1. the heading is *"Compare this story"*, never "See the other side" — which would frame the
 *     reader's own reading as a side to be corrected;
 *  2. ratings are stated as sourced placement — *"is rated right of centre"* — where the passive
 *     points at the registry rather than asserting a judgement;
 *  3. **both** outlets are named on the same axis, because rating only the sibling would imply the
 *     article the reader chose is the neutral one;
 *  4. no corrective verbs: never balance, correct, counter, or "the full picture". The offer is a
 *     comparison, and the comparison is the reader's to make.
 */
export function ContinuationStrip({
  anchorUrl,
  showAllOutlets = true,
  surface = "card",
}: {
  anchorUrl: string;
  /** Render the "View all N outlets" link. False on the story page itself, where that link would
   *  point at the page the reader is already reading — an offer to go where they are. The rest of
   *  the strip still earns its place there: the coverage list is ordered by recency, not by "what
   *  is opposite to the thing you just read", so naming one specific outlet is a real shortcut
   *  through forty rows. */
  showAllOutlets?: boolean;
  /** Which surface this instance sits on, carried on `continuation_shown` (design §7). The point
   *  of the field is comparing them: the story page's rows are cluster members by construction, so
   *  its armed→shown ratio should differ sharply from Discover's, and one blended number would
   *  hide that. */
  surface?: "card" | "story";
}) {
  const { t } = useTranslation();
  const recordRead = useRecordRead();
  const [offer, setOffer] = React.useState<Continuation | null>(null);
  const [armedFor, setArmedFor] = React.useState<{ armedAt: number } | null>(null);

  // Is THIS card the one the reader just opened from? Recomputed only when arming changes, so a
  // page of sixty cards costs sixty cheap in-memory subscriptions rather than sixty DOM listeners.
  const sync = React.useCallback(() => {
    const armed = readArmed();
    setArmedFor(armed && armed.anchorUrl === anchorUrl ? { armedAt: armed.armedAt } : null);
  }, [anchorUrl]);

  React.useEffect(() => {
    sync();
    return subscribeArmed(sync);
  }, [sync]);

  const onReturn = React.useCallback(
    (hiddenMs: number) => {
      const armed = readArmed();
      if (!armed || armed.anchorUrl !== anchorUrl) return;

      const sinceRead = Date.now() - armed.armedAt;
      if (sinceRead > FRESHNESS_MS) {
        // Reported, not silent. These two branches consume a qualifying return and render nothing,
        // which from outside is indistinguishable from the return never being detected at all — and
        // those have different causes and different fixes. A `suppressed` event says which.
        track("continuation_suppressed", { storyId: armed.offer.storyId, reason: "stale" });
        clearArmed(); // past the moment; the feed slot covers a later session
        return;
      }
      if (!mayShow(armed.offer.storyId)) {
        // `recordImpression` writes to localStorage whether or not the analytics event survives, so
        // impressions accumulated while these events were being dropped by the sink. A story can
        // therefore sit at the cap with no record of ever having been shown.
        track("continuation_suppressed", {
          storyId: armed.offer.storyId,
          reason: readState()[armed.offer.storyId]?.d === 1 ? "dismissed" : "capped",
        });
        clearArmed(); // dismissed, or already shown twice without engagement
        return;
      }
      const impressionIndex = recordImpression(armed.offer.storyId);
      setOffer(armed.offer);
      track("continuation_shown", {
        storyId: armed.offer.storyId,
        hiddenMs,
        minutesSinceRead: Math.round(sinceRead / 60_000),
        impressionIndex,
        distance: armed.offer.distance,
        surface,
      });
    },
    [anchorUrl, surface],
  );

  // Only the armed card listens. Every other card passes `enabled: false` and attaches nothing.
  useVisibilityReturn(onReturn, { minHiddenMs: MIN_HIDDEN_MS, enabled: armedFor !== null });

  if (!offer) return null;

  const sideKey = (bucket: string | null) =>
    bucket === "right" ? "continuation.side.right" : "continuation.side.left";
  const siblingSide = t(sideKey(offer.sibling.leanBucket));
  const anchorSide = t(sideKey(offer.anchor.leanBucket));

  const dismiss = () => {
    dismissStory(offer.storyId);
    clearArmed();
    track("continuation_dismissed", { storyId: offer.storyId });
    setOffer(null);
  };

  const open = () => {
    recordRead.mutate({
      url: offer.sibling.url,
      title: offer.sibling.headline,
      openedFrom: "continuation",
    });
    track("continuation_opened", {
      storyId: offer.storyId,
      distance: offer.distance,
      minutesSinceRead: armedFor ? Math.round((Date.now() - armedFor.armedAt) / 60_000) : null,
    });
    clearArmed();
    setOffer(null);
    window.open(offer.sibling.url, "_blank", "noopener,noreferrer");
  };

  return (
    <section
      // motion-safe:animate-in keeps the entrance for readers who want it and skips it for
      // prefers-reduced-motion, which Tailwind's motion-safe variant already encodes.
      className="mt-3 rounded-lg border border-primary/20 bg-primary/5 p-3 motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-top-1"
      aria-label={t("continuation.title")}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold">
          <ArrowLeftRight className="h-4 w-4 text-primary" aria-hidden />
          {t("continuation.title")}
        </h3>
        <button
          type="button"
          onClick={dismiss}
          aria-label={t("continuation.dismiss")}
          className="-m-1 rounded p-1 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>

      <p className="mt-1.5 text-sm text-muted-foreground">
        {t("continuation.body", {
          outlets: offer.outlets,
          publisher: offer.sibling.publisher,
          siblingSide,
          anchorSide,
        })}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="button"
          onClick={open}
          className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          {t("continuation.cta")}
        </button>
        {showAllOutlets ? (
        <Link
          href={`/stories/${encodeURIComponent(offer.storyId)}`}
          onClick={() => track("continuation_all_outlets", { storyId: offer.storyId })}
          className="inline-flex items-center gap-1 rounded text-xs font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          {t("continuation.allOutlets", { n: offer.outlets })}
          <ArrowRight className="h-3.5 w-3.5" aria-hidden />
        </Link>
        ) : null}
      </div>
    </section>
  );
}
