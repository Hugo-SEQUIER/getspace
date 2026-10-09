import { createFileRoute } from "@tanstack/react-router";
import { SettingsView } from "@web/app/components/space/workspace-pages";
export const Route = createFileRoute("/app/settings/$id")({ component: Page });
function Page() { const { id } = Route.useParams(); return <SettingsView base="/app" tab={id} />; }
