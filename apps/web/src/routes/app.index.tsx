import { createFileRoute } from "@tanstack/react-router";
import { HomeView } from "@web/app/components/space/workspace-pages";
import { useShellActions } from "@web/app/components/space/shell-actions";
export const Route = createFileRoute("/app/")({ component: Home });
function Home() { const actions = useShellActions(); return <HomeView base="/app" {...actions} />; }
