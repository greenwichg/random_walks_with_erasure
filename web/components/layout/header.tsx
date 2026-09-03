"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession, signOut } from "next-auth/react";
import { Menu, Puzzle, ScanSearch, Search } from "lucide-react";
import { Logo } from "@/components/layout/logo";
import { NavLinks } from "@/components/layout/nav-links";
import { DesktopNav } from "@/components/layout/desktop-nav";
import { DesktopMenu } from "@/components/layout/desktop-menu";
import { NotificationsMenu } from "@/components/layout/notifications-menu";
import { SearchCommand } from "@/components/layout/search-command";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { NAV_FLAT } from "@ih/core/logic/nav";
import { useTranslation } from "@/lib/i18n";

/** Today's date in the reader's locale, resolved after mount (SSR has no timezone to trust). */
function useToday(): string {
  const [today, setToday] = React.useState("");
  React.useEffect(() => {
    setToday(
      new Date().toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" }),
    );
  }, []);
  return today;
}

/**
 * The sticky masthead. On desktop (lg+) it is laid out to the reference: a thin top strip (tools
 * on the left, today's date on the right), then the bar — menu button · wordmark · Home / For You /
 * Local / Blind spots · search field · notifications · theme · "My account". The inner rows share
 * the page's centred column, so the bar's edges are the content's edges.
 *
 * Below lg the bar is exactly what it was: drawer trigger · page label · search icon ·
 * notifications · theme · account menu. The page label shows only below lg: on desktop the nav's
 * rule names the section and the page's own <h1> names the page.
 */
export function Header() {
  const pathname = usePathname();
  const { data: session } = useSession();
  const name = session?.user?.name ?? "Guest";
  const email = session?.user?.email ?? "";
  const image = session?.user?.image ?? "";
  const initials =
    name
      .split(" ")
      .map((s) => s.charAt(0))
      .filter(Boolean)
      .slice(0, 2)
      .join("")
      .toUpperCase() || "U";
  const [mobileNav, setMobileNav] = React.useState(false);
  const [searchOpen, setSearchOpen] = React.useState(false);
  const today = useToday();

  const { t } = useTranslation();
  const current = NAV_FLAT.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)));
  // The two report notifications land on period pages that live UNDER /report, so the nav lookup
  // above matches the "Health Report" item by prefix and labels them with it — a reader who clicked
  // "Weekly report ready" would land on a page headed "Weekly report" under a bar reading "Health
  // Report". Same situation as a publisher profile: a real destination that is not a nav item.
  const reportPeriod = /^\/report\/(weekly|monthly)$/.exec(pathname)?.[1];

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    // On desktop the bar is a tile like every other surface (card, not page) over the grey page;
    // below lg it is the page-tinted glass it always was.
    <header className="glass safe-top sticky top-0 z-20 border-b lg:bg-card/85">
      {/* Desktop top strip — the reference's utility line above the bar. Below lg the utility bar
          renders under the header instead (chrome-slots.tsx), as it always has. */}
      <div className="hidden border-b lg:block">
        <div className="mx-auto flex h-8 w-full max-w-6xl items-center justify-between px-8 text-[11px] text-muted-foreground">
          <div className="flex items-center gap-4">
            <Link href="/settings" className="inline-flex items-center gap-1.5 rounded transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <Puzzle className="h-3 w-3" aria-hidden />
              {t("home.utility.extension")}
            </Link>
            <Link href="/analyze" className="inline-flex items-center gap-1.5 rounded transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              <ScanSearch className="h-3 w-3" aria-hidden />
              {t("home.footer.analyze")}
            </Link>
          </div>
          <time aria-live="off" className="tabular-nums">{today}</time>
        </div>
      </div>

      <div className="mx-auto flex min-h-[4rem] w-full max-w-6xl items-center gap-3 px-4 lg:px-8">
        {/* Mobile nav */}
        <Sheet open={mobileNav} onOpenChange={setMobileNav}>
          <SheetTrigger asChild>
            {/* No size class on the icon: Button's base sets `[&_svg]:size-4`, a DESCENDANT selector
                (0,1,1) that outranks a utility class on the svg itself (0,1,0). Every `h-5 w-5` and
                `h-[1.15rem]` written on a header icon was silently rendering at 16px — verified in a
                browser. Removing them makes the code say what actually happens, so the next person to
                change an icon size edits the one place that governs it. Zero visual change. */}
            <Button variant="ghost" size="icon" className="lg:hidden" aria-label={t("header.openMenu")}>
              <Menu />
            </Button>
          </SheetTrigger>
          <SheetContent side="left" className="safe-top w-72 p-0">
            <div className="flex h-16 items-center px-6">
              <Logo />
            </div>
            <NavLinks onNavigate={() => setMobileNav(false)} />
          </SheetContent>
        </Sheet>

        {/* Desktop masthead: the slide-out menu's button, the wordmark, then the four section
            links (all lg+ only). */}
        <DesktopMenu />
        <Link
          href="/"
          aria-label={t("sidebar.homeAria")}
          className="hidden shrink-0 rounded lg:block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
        >
          <Logo />
        </Link>
        <React.Suspense fallback={null}>
          <DesktopNav />
        </React.Suspense>

        {/* Current-page label (below lg only) — NOT an <h1>: each page renders its own primary
            heading, so this stays a plain label to keep a single <h1> landmark per page
            (accessibility). Publisher profiles are contextual destinations outside the nav, so they
            carry their own label instead of falling back to "Home". */}
        <span className="hidden text-lg font-semibold tracking-tight sm:block lg:hidden">
          {pathname.startsWith("/publishers")
            ? t("publishers.header")
            : reportPeriod
              ? t(`report.period.${reportPeriod}.title`)
              : t(current?.labelKey ?? "nav.dashboard")}
        </span>

        <div className="ml-auto flex shrink-0 items-center gap-1.5">
          {/* Not a <Button>: it is a pill with a label and a ⌘K hint, so it composes its own box.
              That means it must ALSO restate the focus ring and the radius Button provides — it had
              neither, so it was the one header control with no visible keyboard focus and the only
              one at `rounded-lg` while every sibling sits at `rounded-md`.

              On desktop it reads as the search FIELD in the reference's bar — a fixed width and the
              real placeholder — while still opening the ⌘K overlay, which is the one search
              implementation. Below lg it is the compact "Search ⌘K" pill it always was. */}
          <button
            onClick={() => setSearchOpen(true)}
            aria-label={t("header.search")}
            className="hidden h-9 shrink-0 items-center gap-2 rounded-md border bg-background/60 px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:flex lg:w-56 xl:w-72"
          >
            <Search className="h-4 w-4 shrink-0" />
            <span className="lg:hidden">{t("header.search")}</span>
            <span className="hidden min-w-0 flex-1 truncate text-left lg:inline">{t("header.search")}</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-[0.65rem]">⌘K</kbd>
          </button>
          <Button variant="ghost" size="icon" className="text-muted-foreground sm:hidden" onClick={() => setSearchOpen(true)} aria-label={t("header.search")}>
            <Search />
          </Button>

          <NotificationsMenu />

          <ThemeToggle />

          {/* `modal={false}`. A Radix menu is modal by default, which engages `react-remove-scroll`
              and marks the rest of the document `aria-hidden` — so while this menu was open the
              header, avatar included, was not in the accessibility tree at all. That is the reported
              "becomes inaccessible", literally: a screen reader could not reach the control whose own
              menu was showing, and `getByRole` could not find it either.

              Isolated by rebuilding with `modal={true}`: the aria-hidden assertion was the one that
              failed, while the test whose only assertion is scroll preservation passed. (An earlier
              note here claimed the page was also thrown to the top and never restored. It was not —
              that measurement clicked an off-screen trigger, and Playwright scrolls a target into
              view before clicking. Retracted.)

              A profile menu is navigation, not a modal; it should never have been trapping focus or
              hiding the document from assistive tech.

              Scoped to this menu and the bell rather than defaulted in ui/dropdown-menu.tsx: making
              every dropdown non-modal was tried and MEASURED to break the Stories filter reset —
              without the modal layer the menu dismisses when the router navigation from the previous
              pick lands, so "pick Left, then pick All" could not complete. The filters keep modal
              semantics and are covered instead by the CSS override in globals.css.

              The trigger is the avatar below lg (unchanged) and, on desktop, the reference's
              outlined "My account" button — the same menu behind both. */}
          <DropdownMenu modal={false}>
            <DropdownMenuTrigger asChild>
              {/* `ml-1` was extra spacing on top of the row's own `gap-1.5`, making this the one
                  control set further from its neighbour than they are from each other. The focus ring
                  also lacked `ring-offset-2`, which every Button in this row has — so the avatar's
                  ring sat flush against the image while the others floated 2px clear. */}
              <button
                aria-label={name}
                className="touch-target grid h-9 place-items-center rounded-full outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 lg:h-9 lg:rounded-md lg:border lg:bg-background/60 lg:px-3 lg:text-sm lg:font-medium lg:transition-colors lg:hover:bg-accent"
              >
                <span className="lg:hidden">
                  <Avatar>
                    <AvatarImage src={image} alt={name} />
                    <AvatarFallback>{initials}</AvatarFallback>
                  </Avatar>
                </span>
                <span className="hidden whitespace-nowrap lg:inline">{t("home.menu.myAccount")}</span>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>
                <div className="flex flex-col">
                  <span className="text-sm font-medium text-foreground">{name}</span>
                  {email ? (
                    <span className="text-xs font-normal text-muted-foreground">{email}</span>
                  ) : null}
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              {/* The reader's own surfaces (Template-4 user panel) — every entry a real route. */}
              <DropdownMenuItem asChild>
                <Link href="/report">{t("nav.report")}</Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/saved">{t("nav.saved")}</Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/history">{t("nav.history")}</Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild>
                <Link href="/profile">{t("nav.profile")}</Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/settings">{t("nav.settings")}</Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-destructive focus:text-destructive"
                onClick={() => signOut({ callbackUrl: "/signin" })}
              >
                {t("header.signOut")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <SearchCommand open={searchOpen} onOpenChange={setSearchOpen} />
    </header>
  );
}
