import { createFileRoute } from "@tanstack/react-router";
import { SystemsView } from "@web/app/components/space/systems-view";
export const Route = createFileRoute("/app/systems/")({ component: () => <SystemsView base="/app" /> });
