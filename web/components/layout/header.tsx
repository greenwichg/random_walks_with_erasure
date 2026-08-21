"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSession, signOut } from "next-auth/react";
import { Menu, Search } from "lucide-react";
import { Logo } from "@/components/layout/logo";
import { NavLinks } from "@/components/layout/nav-links";
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
import { NAV_FLAT } from "@/lib/nav";
import { useTranslation } from "@/lib/i18n";

/** Sticky top bar: mobile nav trigger, page title, search (⌘K), theme, profile. */
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
    <header className="glass safe-top sticky top-0 z-20 flex min-h-[4rem] items-center gap-3 border-b px-4 lg:px-8">
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

      {/* Current-page label — NOT an <h1>: each page renders its own primary heading, so this stays a
          plain label to keep a single <h1> landmark per page (accessibility). Publisher profiles are
          contextual destinations outside the nav, so they carry their own label instead of falling
          back to "Home". */}
      <span className="hidden text-lg font-semibold tracking-tight sm:block">
        {pathname.startsWith("/publishers")
          ? t("publishers.header")
          : reportPeriod
            ? t(`report.period.${reportPeriod}.title`)
            : t(current?.labelKey ?? "nav.dashboard")}
      </span>

      <div className="ml-auto flex items-center gap-1.5">
        {/* Not a <Button>: it is a pill with a label and a ⌘K hint, so it composes its own box.
            That means it must ALSO restate the focus ring and the radius Button provides — it had
            neither, so it was the one header control with no visible keyboard focus and the only
            one at `rounded-lg` while every sibling sits at `rounded-md`. */}
        <button
          onClick={() => setSearchOpen(true)}
          aria-label={t("header.search")}
          className="hidden h-9 items-center gap-2 rounded-md border bg-background/60 px-3 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:flex"
        >
          <Search className="h-4 w-4" />
          <span>{t("header.search")}</span>
          <kbd className="rounded border bg-muted px-1.5 py-0.5 text-[0.65rem]">⌘K</kbd>
        </button>
        <Button variant="ghost" size="icon" className="text-muted-foreground sm:hidden" onClick={() => setSearchOpen(true)} aria-label={t("header.search")}>
          <Search />
        </Button>

        <NotificationsMenu />

        <ThemeToggle />

        {/* `modal={false}`, and the cost of the default is MEASURED rather than assumed.
            A Radix menu is modal by default, which engages `react-remove-scroll`: it sets
            `overflow: hidden` on <body> and marks the rest of the document `aria-hidden`. Against
            the real build, at scrollY 381:

              open   -> scrollY 0, header inside aria-hidden="true"
              closed -> scrollY 0                     the position is DISCARDED, never restored

            So a reader scrolled into the page who clicked the avatar was thrown back to the top and
            could not get back, and while the menu was open the header — avatar included — was not
            in the accessibility tree at all. That is the reported "disappears or becomes
            inaccessible", both halves of it.

            It bites here and not on a stock page because of `html { overflow-x: clip }` in
            globals.css: body's overflow only propagates to the viewport when the root element's
            overflow is `visible` in both axes, so the lock lands on <body> itself. The clip rule
            earns its keep (see the note there), so the menu gives way instead.

            Scoped to this menu and the bell rather than defaulted in ui/dropdown-menu.tsx: making
            every dropdown non-modal was tried and MEASURED to break the Stories filter reset —
            without the modal layer the menu dismisses when the router navigation from the previous
            pick lands, so "pick Left, then pick All" could not complete. FilterSelect therefore
            keeps the default, and keeps this bug; see docs/HEADER_MENU_SCROLL.md. */}
        <DropdownMenu modal={false}>
          <DropdownMenuTrigger asChild>
            {/* `ml-1` was extra spacing on top of the row's own `gap-1.5`, making this the one
                control set further from its neighbour than they are from each other. The focus ring
                also lacked `ring-offset-2`, which every Button in this row has — so the avatar's
                ring sat flush against the image while the others floated 2px clear. */}
            <button
              aria-label={name}
              className="touch-target grid h-9 w-9 place-items-center rounded-full outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              <Avatar>
                <AvatarImage src={image} alt={name} />
                <AvatarFallback>{initials}</AvatarFallback>
              </Avatar>
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

      <SearchCommand open={searchOpen} onOpenChange={setSearchOpen} />
    </header>
  );
}
