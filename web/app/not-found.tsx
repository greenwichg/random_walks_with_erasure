import Link from "next/link";
import { ArrowLeft, Compass } from "lucide-react";
import { Logo } from "@/components/layout/logo";
import { Button } from "@/components/ui/button";

/** Branded 404 — rendered at the root, outside the app shell. */
export default function NotFound() {
  return (
    <div className="grid min-h-screen place-items-center px-4">
      <div className="flex max-w-md flex-col items-center text-center">
        <Logo className="mb-8" />
        <p className="text-6xl font-semibold tracking-tight text-primary">404</p>
        <h1 className="mt-3 text-xl font-semibold tracking-tight">This page took a detour</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          The page you're looking for doesn't exist or may have moved. Let's get you back on track.
        </p>
        <div className="mt-6 flex items-center gap-3">
          <Button asChild>
            <Link href="/">
              <ArrowLeft className="h-4 w-4" /> Back to dashboard
            </Link>
          </Button>
          <Button variant="outline" asChild>
            <Link href="/discover">
              <Compass className="h-4 w-4" /> Explore stories
            </Link>
          </Button>
        </div>
      </div>
    </div>
  );
}
