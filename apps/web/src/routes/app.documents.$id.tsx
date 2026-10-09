import { createFileRoute } from "@tanstack/react-router";
import { DocumentsView } from "@web/app/components/space/documents-view";
export const Route = createFileRoute("/app/documents/$id")({ component: Page });
function Page() { const { id } = Route.useParams(); return <DocumentsView base="/app" id={id} />; }
