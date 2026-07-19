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
    <header className="glass sticky top-0 z-20 flex h-16 items-center gap-3 border-b px-4 lg:px-8">
      {/* Mobile nav */}
      <Sheet open={mobileNav} onOpenChange={setMobileNav}>
        <SheetTrigger asChild>
          <Button variant="ghost" size="icon" className="lg:hidden" aria-label={t("header.openMenu")}>
            <Menu className="h-5 w-5" />
          </Button>
        </SheetTrigger>
        <SheetContent side="left" className="w-72 p-0">
          <div className="flex h-16 items-center px-6">
            <Logo />
          </div>
          <NavLinks onNavigate={() => setMobileNav(false)} />
        </SheetContent>
      </Sheet>

      <h1 className="hidden text-lg font-semibold tracking-tight sm:block">{t(current?.labelKey ?? "nav.dashboard")}</h1>

      <div className="ml-auto flex items-center gap-1.5">
        <button
          onClick={() => setSearchOpen(true)}
          className="hidden h-9 items-center gap-2 rounded-lg border bg-background/60 px-3 text-sm text-muted-foreground transition-colors hover:bg-accent sm:flex"
        >
          <Search className="h-4 w-4" />
          <span>{t("header.search")}</span>
          <kbd className="rounded border bg-muted px-1.5 py-0.5 text-[0.65rem]">⌘K</kbd>
        </button>
        <Button variant="ghost" size="icon" className="text-muted-foreground sm:hidden" onClick={() => setSearchOpen(true)} aria-label={t("header.search")}>
          <Search className="h-5 w-5" />
        </Button>

        <NotificationsMenu />

        <ThemeToggle />

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="ml-1 rounded-full outline-none ring-offset-background focus-visible:ring-2 focus-visible:ring-ring">
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
