import { createFileRoute } from "@tanstack/react-router";
import { SearchView } from "@web/app/components/space/workspace-pages";
export const Route = createFileRoute("/app/search/")({ component: () => <SearchView base="/app" /> });
