import { createFileRoute } from "@tanstack/react-router";
import { ProjectsView } from "@web/app/components/space/workspace-pages";
export const Route = createFileRoute("/app/projects/$id")({ component: Page });
function Page() { const { id } = Route.useParams(); return <ProjectsView base="/app" id={id} />; }
