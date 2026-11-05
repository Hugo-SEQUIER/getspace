import { createFileRoute } from "@tanstack/react-router";
import { SystemsView } from "@web/app/components/space/systems-view";
export const Route = createFileRoute("/app/systems/$id")({ component: Page });
function Page() { const { id } = Route.useParams(); return <SystemsView base="/app" id={id} />; }
