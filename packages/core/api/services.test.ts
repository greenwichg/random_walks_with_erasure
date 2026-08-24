// The durable feedback identity (feedbackArticleId).
//
// Corpus item ids are positional — `Q{i}` is a catalog-export row index, re-minted every corpus
// refresh — so a feedback signal recorded under one silently stops applying (or mis-applies)
// after the next refresh. The canonical URL survives generations; the engine's
// feedback_id_translator resolves it back to the serving corpus's id. This helper is the ONE
// place clients derive the identity they record and remove feedback under.
import { test } from "node:test";
import assert from "node:assert/strict";
import { feedbackArticleId } from "./services.ts";

test("the durable identity is the canonical URL when the card carries one", () => {
  assert.equal(
    feedbackArticleId({ id: "Q80484", url: "https://news.example.com/story" }),
    "https://news.example.com/story",
  );
});

test("without a URL the raw id is the honest fallback", () => {
  // Synthetic/demo corpora carry no URL map; their ids are stable within a process and the
  // engine's translator passes bare ids through untouched.
  assert.equal(feedbackArticleId({ id: "S17" }), "S17");
  assert.equal(feedbackArticleId({ id: "S17", url: undefined }), "S17");
  assert.equal(feedbackArticleId({ id: "S17", url: null }), "S17");
  assert.equal(feedbackArticleId({ id: "S17", url: "" }), "S17", "an empty URL is no URL");
});
