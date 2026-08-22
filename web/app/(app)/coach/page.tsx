"use client";

import * as React from "react";
import { Send, Sparkles } from "lucide-react";
import type { CoachMessage } from "@ih/core/domain/types";
import { services } from "@ih/core/api/services";
import { useTranslation } from "@/lib/i18n";
import { useCoachHistory, useFeedback, useOpenRecommendation } from "@/hooks/use-data";
import { lastEcho, activeFollowUps } from "@ih/core/logic/coach-presentation";
import { CoachMessageBubble, CoachTyping } from "@/components/coach/coach-message";
import { Button } from "@/components/ui/button";

const SUGGESTION_KEYS = ["coach.s1", "coach.s2", "coach.s3", "coach.s4", "coach.s5", "coach.s6"];

export default function CoachPage() {
  const { data: history } = useCoachHistory();
  const { t } = useTranslation();
  const [messages, setMessages] = React.useState<CoachMessage[]>([]);
  const [input, setInput] = React.useState("");
  const [thinking, setThinking] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const seeded = React.useRef(false);
  // embedded-card feedback + reception: the SAME mutations the recommendations page uses
  const feedback = useFeedback();
  const openRec = useOpenRecommendation();

  // Seed the transcript from history once.
  React.useEffect(() => {
    if (history && !seeded.current) {
      setMessages(history);
      seeded.current = true;
    }
  }, [history]);

  React.useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, thinking]);

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || thinking) return;
    const userMsg: CoachMessage = {
      id: `u_${Date.now()}`,
      role: "user",
      content,
      createdAt: new Date().toISOString(),
    };
    // Coach v2: round-trip the latest structured echo so "it" / "the first one" resolve
    // server-side. Undefined on v1 transcripts — the request stays exactly today's shape.
    const echo = lastEcho(messages);
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setThinking(true);
    try {
      const reply = await services.coachSend(content, echo);
      setMessages((m) => [...m, reply]);
    } catch {
      setMessages((m) => [
        ...m,
        {
          id: `e_${Date.now()}`,
          role: "assistant",
          content: t("coach.error"),
          createdAt: new Date().toISOString(),
        },
      ]);
    } finally {
      setThinking(false);
    }
  };

  // Coach v2 offers reply-specific follow-ups; the static starters keep their exact v1
  // behaviour (first turn only) and never compete with a live offer.
  const followUps = thinking ? null : activeFollowUps(messages);
  const showSuggestions = messages.length <= 1 && !followUps;

  return (
    <div className="mx-auto flex h-[calc(100vh-4rem)] w-full max-w-3xl flex-col px-4 sm:px-6">
      {/* transcript
          The scrollbar is hidden (the same idiom as the trending rail), not the scrolling: wheel,
          touch and keyboard all still work. Hiding it does remove the affordance that says "there
          is more above", so the keyboard route has to be explicit rather than left to the browser
          — `tabIndex` makes the region focusable in Safari and older Chrome, which do not focus a
          scrollable div on their own, and `role="log"` gives it a name and announces new replies.
          Drop one and you have a pane a keyboard user cannot reach and cannot see the state of. */}
      <div
        ref={scrollRef}
        tabIndex={0}
        role="log"
        aria-label={t("coach.title")}
        className="flex-1 space-y-6 overflow-y-auto py-6 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        <div className="mb-2 flex flex-col items-center gap-2 text-center">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-glow">
            <Sparkles className="h-5 w-5" />
          </div>
          <h1 className="text-lg font-semibold tracking-tight">{t("coach.title")}</h1>
          <p className="max-w-sm text-sm text-muted-foreground">{t("coach.subtitle")}</p>
        </div>

        {messages.map((m) => (
          <CoachMessageBubble
            key={m.id}
            message={m}
            onCardAction={(articleId, action) => feedback.mutate({ articleId, action })}
            onCardOpen={(rec) =>
              openRec.mutate({ articleId: rec.article.id, crossCutting: rec.crossCutting })
            }
          />
        ))}
        {thinking && <CoachTyping />}
      </div>

      {/* suggestions + input */}
      <div className="space-y-3 pb-6">
        {followUps && (
          <div className="flex flex-wrap gap-2" aria-label={t("coach.followUps")}>
            {followUps.map((f) => (
              <button
                key={f}
                onClick={() => send(f)}
                className="rounded-full border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                {f}
              </button>
            ))}
          </div>
        )}
        {showSuggestions && (
          <div className="flex flex-wrap gap-2">
            {SUGGESTION_KEYS.map((key) => (
              <button
                key={key}
                onClick={() => send(t(key))}
                className="rounded-full border bg-card px-3 py-1.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              >
                {t(key)}
              </button>
            ))}
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(input);
          }}
          className="flex items-end gap-2 rounded-2xl border bg-card p-2 shadow-soft focus-within:ring-2 focus-within:ring-ring"
        >
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
            rows={1}
            placeholder={t("coach.placeholder")}
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
          />
          <Button type="submit" size="icon" disabled={!input.trim() || thinking} aria-label={t("coach.send")}>
            <Send className="h-4 w-4" />
          </Button>
        </form>
        <p className="text-center text-[0.7rem] text-muted-foreground">{t("coach.footer")}</p>
      </div>
    </div>
  );
}
