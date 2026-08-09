"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

/** Light/dark toggle with a crossfade. Avoids hydration mismatch by waiting for mount. */
export function ThemeToggle() {
  const { t } = useTranslation();
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  const isDark = resolvedTheme === "dark";

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={t("common.toggleTheme")}
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className="text-muted-foreground"
    >
      {mounted ? (
        // No size class needed: Button's `[&_svg]:size-4` governs every svg inside it, and being a
        // descendant selector it outranks a utility on the icon anyway.
        isDark ? <Sun /> : <Moon />
      ) : (
        // The placeholder DOES need an explicit size — it is a <div>, so `[&_svg]:size-4` does not
        // reach it. Without one the button collapses before hydration and the whole header row
        // shifts when the theme resolves. (Removing it here was a regression the header e2e caught:
        // the control vanished from the icon-size comparison because it had no box.)
        <div className="h-4 w-4" />
      )}
    </Button>
  );
}
