import type { Config } from "tailwindcss";

/**
 * Design-system source of truth. Colors are CSS variables (see app/globals.css)
 * so light/dark themes swap without touching component code. The `brand` and
 * metric hues are the product palette used across HealthCard / charts / badges.
 */
const config: Config = {
  darkMode: ["class"],
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    container: {
      center: true,
      padding: "1.5rem",
      screens: { "2xl": "1400px" },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        // Political spectrum + metric semantics (stable across themes).
        //
        // Named `lean-*`, NOT `left`/`center`/`right`: Tailwind derives utility names from these
        // keys, and `left`/`center`/`right` collide head-on with its own built-in utilities.
        // Measured in the generated CSS, the loser differed per property — `.text-left` resolved
        // to `color` (so 60 alignment usages across the app silently painted lean hues and never
        // aligned), while `.bg-left` resolved to `background-position` (so the report's metric
        // bars silently lost their fill). Both classes of bug are impossible under a prefixed
        // name. The CSS variables stay `--left`/`--center`/`--right`: they are read directly by
        // lib/metrics.ts, the charts, and coverage-plate's dynamic `hsl(var(--${token}))`.
        "lean-left": "hsl(var(--left))",
        "lean-center": "hsl(var(--center))",
        "lean-right": "hsl(var(--right))",
        positive: "hsl(var(--positive))",
        caution: "hsl(var(--caution))",
        negative: "hsl(var(--negative))",
      },
      borderRadius: {
        xl: "calc(var(--radius) + 4px)",
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        // Headline face (globals.css sets it on h1–h3 by default); the utility is for the
        // non-heading headline moments — quoted framing headlines, the coverage plate's count.
        display: ["var(--font-display)", "var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
      boxShadow: {
        soft: "0 1px 2px 0 rgb(0 0 0 / 0.04), 0 1px 3px 0 rgb(0 0 0 / 0.06)",
        card: "0 1px 3px 0 rgb(0 0 0 / 0.05), 0 8px 24px -12px rgb(0 0 0 / 0.12)",
        glow: "0 0 0 1px hsl(var(--border)), 0 12px 40px -16px hsl(var(--primary) / 0.35)",
      },
      keyframes: {
        "accordion-down": {
          from: { height: "0" },
          to: { height: "var(--radix-accordion-content-height)" },
        },
        "accordion-up": {
          from: { height: "var(--radix-accordion-content-height)" },
          to: { height: "0" },
        },
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        shimmer: {
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "fade-in": "fade-in 0.4s ease-out both",
        shimmer: "shimmer 1.6s infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
