import { createFileRoute } from "@tanstack/react-router";
import { TasksView } from "@web/app/components/space/resource-views";
export const Route = createFileRoute("/app/tasks/")({ component: () => <TasksView base="/app" /> });
