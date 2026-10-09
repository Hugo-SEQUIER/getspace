import { createFileRoute, redirect } from "@tanstack/react-router";

// A self-hosted Space has no public website: its root opens the workspace.
export const Route = createFileRoute("/")({ beforeLoad: () => { throw redirect({ href: "/app" }); } });
