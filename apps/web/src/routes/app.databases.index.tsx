import { createFileRoute } from "@tanstack/react-router";
import { DatabasesView } from "@web/app/components/space/databases-view";
export const Route = createFileRoute("/app/databases/")({ component: () => <DatabasesView base="/app" /> });
