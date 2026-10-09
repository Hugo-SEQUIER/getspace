import { createFileRoute } from "@tanstack/react-router";
import { WorkspaceLayout } from "../workspace-layout";
export const Route = createFileRoute("/app")({ component: WorkspaceLayout, ssr: false });
