import { createFileRoute } from "@tanstack/react-router";
import { ProjectsView } from "@web/app/components/space/workspace-pages";
export const Route = createFileRoute("/app/projects/")({ component: () => <ProjectsView base="/app" /> });
