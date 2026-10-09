import { createFileRoute } from "@tanstack/react-router";
import { AgentsView } from "@web/app/components/space/workspace-pages";
export const Route = createFileRoute("/app/agents/")({ component: () => <AgentsView  /> });
