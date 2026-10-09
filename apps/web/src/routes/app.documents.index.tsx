import { createFileRoute } from "@tanstack/react-router";
import { DocumentsView } from "@web/app/components/space/documents-view";
export const Route = createFileRoute("/app/documents/")({ component: () => <DocumentsView base="/app" /> });
