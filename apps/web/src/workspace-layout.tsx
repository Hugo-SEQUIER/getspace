import { Outlet, useLocation } from "@tanstack/react-router";
import { SpaceProvider } from "@web/app/components/space/provider";
import { SidebarPreferencesProvider } from "@web/app/components/space/sidebar-preferences";
import { SpaceApp } from "@web/app/components/space/app";
import "@web/app/space.css";
import "@web/app/avatars.css";
import "@web/app/memory-graph.css";
import "@web/app/documents.css";
import "@web/app/databases.css";
import "@web/app/imports.css";
import "@web/app/sidebar.css";
import "@web/app/modals.css";
import "@web/app/refined-workspace.css";
export function WorkspaceLayout() {
  const path = useLocation({ select: location => location.pathname.split("/").slice(2) });
  return <SpaceProvider><SidebarPreferencesProvider><SpaceApp path={path}><Outlet /></SpaceApp></SidebarPreferencesProvider></SpaceProvider>;
}
