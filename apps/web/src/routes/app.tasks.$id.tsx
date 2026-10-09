import { createFileRoute } from "@tanstack/react-router";
import { TasksView } from "@web/app/components/space/resource-views";
export const Route = createFileRoute("/app/tasks/$id")({ component: Page });
function Page() { const { id } = Route.useParams(); return <TasksView base="/app" id={id} />; }
