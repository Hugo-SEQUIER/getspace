import { createFileRoute } from "@tanstack/react-router";
import { spaceIconSvg } from "@web/lib/space/brand-mark";

export const Route = createFileRoute("/icon")({
  server: { handlers: { GET: () => new Response(spaceIconSvg(), {
    headers: { "Content-Type": "image/svg+xml" },
  }) } },
});
