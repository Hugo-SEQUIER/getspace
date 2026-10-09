import { createFileRoute } from "@tanstack/react-router";
import { currentRequestRuntime } from "@web/lib/runtime-storage.server";
import { handleApi } from "@web/app/api/[...path]/route";
export const Route = createFileRoute("/api/$")({ server: { handlers: {
  GET: ({ request, params }) => handleApi(request, params._splat ?? "", currentRequestRuntime()),
  POST: ({ request, params }) => handleApi(request, params._splat ?? "", currentRequestRuntime()),
  DELETE: ({ request, params }) => handleApi(request, params._splat ?? "", currentRequestRuntime()),
  // Unregistered methods otherwise fall through to HTML rendering in Start.
  ANY: ({ request, params }) => handleApi(request, params._splat ?? "", currentRequestRuntime()),
} } });
