import { createFileRoute } from "@tanstack/react-router";
import { OverviewView } from "@web/app/components/space/overview-view";
export const Route = createFileRoute("/app/overview/")({ component: () => <OverviewView base="/app" /> });
