import { createFileRoute } from "@tanstack/react-router";
import { DatabasesView } from "@web/app/components/space/databases-view";
export const Route = createFileRoute("/app/databases/$id")({ component: Page });
function Page() { const { id } = Route.useParams(); return <DatabasesView base="/app" id={id} />; }
