// Writes src/routeTree.gen.ts, the generated route tree that is not committed. `vite dev` and
// `vite build` write it as Vite resolves its configuration, which is all this does, so
// `npm run typecheck` also works on a fresh checkout, before any build.
import { resolveConfig } from "vite";

await resolveConfig({ logLevel: "error" }, "build");
