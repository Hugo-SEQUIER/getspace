import { defineConfig, loadEnv } from "vite";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig(({ mode, command }) => {
  // Server configuration stays in the server process, never in VITE_* defines.
  const env = loadEnv(mode, process.cwd(), "SPACE_");
  for (const [key, value] of Object.entries(env)) process.env[key] ??= value;
  return {
    plugins: [tanstackStart({ router: { codeSplittingOptions: { defaultBehavior: [["component"], ["loader"], ["pendingComponent"], ["errorComponent"], ["notFoundComponent"]] } } }), react()],
    resolve: { alias: { "@web": fileURLToPath(new URL(".", import.meta.url)) } },
    server: { host: "127.0.0.1", port: 3000, strictPort: true },
    preview: { host: "127.0.0.1" },
    // Bundle the production server, so it runs without the source checkout, but let Node
    // load CommonJS dependencies in Vite's development module runner (notably React).
    ssr: { noExternal: command === "build" ? true : undefined },
  };
});
