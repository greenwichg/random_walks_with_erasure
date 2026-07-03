import { cn } from "@/lib/utils";

/** The Information Health mark — a pulse inside a rounded shield. */
export function Logo({ className }: { className?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <div className="relative grid h-8 w-8 place-items-center rounded-[0.7rem] bg-primary text-primary-foreground shadow-glow">
        <svg viewBox="0 0 24 24" fill="none" className="h-[1.15rem] w-[1.15rem]">
          <path
            d="M3 12h3.5l2-5 3 10 2.5-7 1.5 2H21"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <span className="text-[0.95rem] font-semibold tracking-tight">
        Information <span className="text-primary">Health</span>
      </span>
    </div>
  );
}
