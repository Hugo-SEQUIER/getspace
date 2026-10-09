import { createRouter } from "@tanstack/react-router";
import { routeTree } from "./routeTree.gen";
export function getRouter() {
  return createRouter({ routeTree, scrollRestoration: true, defaultPreload: false });
}
// Routes are generated and checked at build time. Dynamic legacy links are
// adapted at the shared navigation boundary during the component migration.
