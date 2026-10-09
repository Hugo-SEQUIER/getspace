import { createRootRoute, HeadContent, Outlet, Scripts } from "@tanstack/react-router";
import { NotificationsProvider } from "@web/app/components/notifications";
import Link from "@web/lib/navigation";
import "@web/app/theme.css";
import "@web/app/globals.css";
import "@web/app/typography.css";
import "@web/app/focus.css";

// Every page of a self-hosted Space is private, so nothing is offered to search engines.
export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Space" },
      { name: "robots", content: "noindex, nofollow" },
    ],
    links: [{ rel: "icon", href: "/favicon.ico" }],
  }),
  component: Root,
  notFoundComponent: () => <main className="source-reader"><h1>Page not found</h1><p><Link href="/app">Open your Space</Link></p></main>,
  errorComponent: () => <main className="source-reader"><h1>This page could not load</h1><p>Please reload and try again.</p><p><a href="/app">Open your Space</a></p></main>,
});
function Root() {
  return <html lang="en"><head><HeadContent /></head><body><NotificationsProvider><Outlet /></NotificationsProvider><Scripts /></body></html>;
}
