import { createFileRoute, Outlet } from "@tanstack/react-router";
import { ConnectFlowProvider } from "@web/app/components/connect/flow-context";
import "@web/app/space.css";
import "@web/app/modals.css";
export const Route = createFileRoute("/connect")({
  head: () => ({ meta: [{ title: "Connect your agent · Space" }, { name: "robots", content: "noindex, nofollow" }, { name: "referrer", content: "no-referrer" }] }),
  component: () => <ConnectFlowProvider><Outlet /></ConnectFlowProvider>,
});
