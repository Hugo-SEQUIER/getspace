import { mkdir, copyFile } from "node:fs/promises";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const directory = new URL("../public/vendor/pdfjs/", import.meta.url);
await mkdir(directory, { recursive: true });
await copyFile(
  require.resolve("pdfjs-dist/build/pdf.worker.min.mjs"),
  new URL("pdf.worker.min.mjs", directory),
);
await copyFile(
  require.resolve("pdfjs-dist/LICENSE"),
  new URL("LICENSE", directory),
);
