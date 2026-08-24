// Wiring pins for the durable feedback identity (see packages/core/api/services.ts,
// feedbackArticleId): every surface that RECORDS feedback must key it through the helper, or the
// signal is stored under a positional corpus id that dies at the next catalog refresh — the bug
// that showed a same-day dislike as "No longer in the catalog" and silently detached it from
// ranking. Source pins in the house dialect (see core-import-guard.test.ts).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const WEB = join(import.meta.dirname, "..");
const read = (p: string) => readFileSync(join(WEB, p), "utf8");

test("the recommendations page records, undoes, and matches by the durable identity", () => {
  const page = read("app/(app)/recommendations/page.tsx");
  assert.ok(
    page.includes("const wireId = feedbackArticleId(rec.article)"),
    "feedback.mutate must send the durable identity, not the positional card id",
  );
  assert.ok(
    page.includes("articleId: wireId"),
    "both the recorded signal and its consequence strip carry the wire identity",
  );
  assert.ok(
    page.includes("cardId: rec.article.id") && page.includes("next.delete(c.cardId)"),
    "undo restores the card through its CARD id — the session dismissed-set's key",
  );
  assert.ok(
    page.includes("!persistedIgnored.has(feedbackArticleId(r.article))"),
    "persisted ignores match the durable identity (and the bare id for legacy rows)",
  );
});

test("the coach's cards record through the same identity", () => {
  const msg = read("components/coach/coach-message.tsx");
  assert.ok(
    msg.includes("onCardAction?.(feedbackArticleId(rec.article), action)"),
    "coach card actions must not record under the positional id",
  );
});
