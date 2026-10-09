import { createFileRoute } from "@tanstack/react-router";
import { MemoryView } from "@web/app/components/space/memory-view";
export const Route = createFileRoute("/app/memory/$id")({ component: Page });
function Page() { const { id } = Route.useParams(); return <MemoryView base="/app" id={id} />; }
