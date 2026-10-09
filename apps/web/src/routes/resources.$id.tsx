import { createFileRoute, useLocation } from "@tanstack/react-router";
import { ResourceReader } from "@web/app/resources/[id]/reader";
import "@web/app/space.css";
import "@web/app/documents.css";
export const Route = createFileRoute("/resources/$id")({ component: Reader });
function Reader() { const { id } = Route.useParams(); const search = useLocation({ select: location => location.searchStr }); const params = new URLSearchParams(search); return <ResourceReader id={id} spaceId={params.get("space_id") ?? undefined} revision={params.get("revision") ?? undefined} />; }
