import { createFileRoute } from "@tanstack/react-router";
import { MemoryView } from "@web/app/components/space/memory-view";
export const Route = createFileRoute("/app/memory/")({ component: () => <MemoryView base="/app" /> });
