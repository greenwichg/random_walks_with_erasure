/**
 * Our own robots.txt.
 *
 * Hidden View asks publishers to publish one and honours what it says (see `examples/robots.py`).
 * Not serving our own would be asking for a courtesy we do not extend — and a site with no
 * robots.txt gives a crawler no way to be told anything.
 *
 * A Route Handler rather than `app/robots.ts`, because Next's metadata convention cannot express
 * the `Sitemap:` line and the comment block below, and the comment is the part a human reads when
 * they arrive here wondering who we are.
 */
export const dynamic = "force-static";

const BODY = `# Hidden View — https://hidden-view.com
# What our own crawlers do, and how to stop them: https://hidden-view.com/crawler
# Our user agents: HiddenView-RSS, HiddenView-Crawler, HiddenView-Robots
# Questions or removal requests: yerram.saisanath@gmail.com

User-agent: *
Allow: /$
Allow: /onboarding
Allow: /privacy
Allow: /crawler
# Everything else is a signed-in reader's own view of their own reading. There is nothing there to
# index, and it is not ours to expose.
Disallow: /
`;

export function GET() {
  return new Response(BODY, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=86400",
    },
  });
}
