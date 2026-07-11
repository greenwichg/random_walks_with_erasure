"use client";

import * as React from "react";
import { Send, Sparkles } from "lucide-react";
import type { CoachMessage } from "@/types/domain";
import { services } from "@/services";
import { useTranslation } from "@/lib/i18n";
import { useCoachHistory } from "@/hooks/use-data";
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
    setMessages((m) => [...m, userMsg]);
    setInput("");
    setThinking(true);
    try {
      const reply = await services.coachSend(content);
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

  const showSuggestions = messages.length <= 1;

  return (
    <div className="mx-auto flex h-[calc(100vh-4rem)] w-full max-w-3xl flex-col px-4 sm:px-6">
      {/* transcript */}
      <div ref={scrollRef} className="flex-1 space-y-6 overflow-y-auto py-6">
        <div className="mb-2 flex flex-col items-center gap-2 text-center">
          <div className="grid h-11 w-11 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-glow">
            <Sparkles className="h-5 w-5" />
          </div>
          <h1 className="text-lg font-semibold tracking-tight">{t("coach.title")}</h1>
          <p className="max-w-sm text-sm text-muted-foreground">{t("coach.subtitle")}</p>
        </div>

        {messages.map((m) => (
          <CoachMessageBubble key={m.id} message={m} />
        ))}
        {thinking && <CoachTyping />}
      </div>

      {/* suggestions + input */}
      <div className="space-y-3 pb-6">
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
