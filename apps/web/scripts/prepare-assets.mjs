import { copyFile } from "node:fs/promises";
const root = new URL("../", import.meta.url);
await copyFile(new URL("app/favicon.ico", root), new URL("public/favicon.ico", root));
