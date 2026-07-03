"use client";

import { motion } from "framer-motion";
import { Bot } from "lucide-react";
import type { CoachMessage as TMessage } from "@/types/domain";
import { METRICS } from "@/lib/metrics";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { LeanBadge } from "@/components/shared/article-badges";
import { cn } from "@/lib/utils";

/** One chat bubble. Assistant messages can carry grounded citations + article suggestions. */
export function CoachMessageBubble({ message }: { message: TMessage }) {
  const isUser = message.role === "user";
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className={cn("flex gap-3", isUser && "flex-row-reverse")}
    >
      {isUser ? (
        <Avatar className="h-8 w-8">
          <AvatarFallback className="bg-secondary text-secondary-foreground">AR</AvatarFallback>
        </Avatar>
      ) : (
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground">
          <Bot className="h-4 w-4" />
        </div>
      )}

      <div className={cn("flex max-w-[85%] flex-col gap-2", isUser && "items-end")}>
        <div
          className={cn(
            "rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
            isUser
              ? "rounded-tr-sm bg-primary text-primary-foreground"
              : "rounded-tl-sm border bg-card",
          )}
        >
          {message.content}
        </div>

        {message.citations && message.citations.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.citations.map((c) => (
              <Badge key={c.metric} variant="secondary" className="font-normal">
                {METRICS[c.metric].label}: <span className="font-semibold">{c.value}</span>
              </Badge>
            ))}
          </div>
        )}

        {message.suggestions && message.suggestions.length > 0 && (
          <div className="mt-1 w-full space-y-2">
            {message.suggestions.map((a) => (
              <div key={a.id} className="rounded-lg border bg-muted/30 p-3">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-medium">{a.publisher}</span>
                  <span>·</span>
                  <span>{a.topic}</span>
                </div>
                <p className="mt-1 text-sm font-medium leading-snug">{a.headline}</p>
                <div className="mt-2">
                  <LeanBadge lean={a.lean} bucket={a.leanBucket} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}

/** Animated "thinking" indicator. */
export function CoachTyping() {
  return (
    <div className="flex gap-3">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-primary text-primary-foreground">
        <Bot className="h-4 w-4" />
      </div>
      <div className="flex items-center gap-1 rounded-2xl rounded-tl-sm border bg-card px-4 py-3.5">
        {[0, 1, 2].map((i) => (
          <motion.span
            key={i}
            className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60"
            animate={{ opacity: [0.3, 1, 0.3], y: [0, -2, 0] }}
            transition={{ duration: 1, repeat: Infinity, delay: i * 0.15 }}
          />
        ))}
      </div>
    </div>
  );
}
