import { createRootRoute, HeadContent, Outlet, Scripts } from "@tanstack/react-router";
import { NotificationsProvider } from "@web/app/components/notifications";
import { StatusPage } from "@web/app/components/status-page";
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
  notFoundComponent: () => <StatusPage title="Page not found"><p>The link may be out of date, or the page has moved.</p></StatusPage>,
  errorComponent: () => <StatusPage title="This page could not load"><p>Please reload and try again.</p></StatusPage>,
});
function Root() {
  return <html lang="en"><head><HeadContent /></head><body><NotificationsProvider><Outlet /></NotificationsProvider><Scripts /></body></html>;
}
