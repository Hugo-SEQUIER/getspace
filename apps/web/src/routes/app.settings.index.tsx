import { createFileRoute } from "@tanstack/react-router";
import { SettingsView } from "@web/app/components/space/workspace-pages";
export const Route = createFileRoute("/app/settings/")({ component: () => <SettingsView base="/app" /> });
