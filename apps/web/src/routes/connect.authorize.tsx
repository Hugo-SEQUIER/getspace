import { createFileRoute } from "@tanstack/react-router";
import { ConsentPage } from "@web/app/components/connect/consent-page";
// An app that signs in through the browser, such as claude.ai, sends the owner here.
export const Route = createFileRoute("/connect/authorize")({ component: () => <ConsentPage /> });
