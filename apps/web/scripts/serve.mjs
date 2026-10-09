import { createReadStream } from "node:fs";
import { readFile, realpath, stat } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseEnv } from "node:util";
import { serve } from "@hono/node-server";

const projectRoot = fileURLToPath(new URL("../", import.meta.url));
const mimeTypes = {
  ".avif": "image/avif", ".css": "text/css; charset=utf-8", ".gif": "image/gif",
  ".html": "text/html; charset=utf-8", ".ico": "image/x-icon", ".jpeg": "image/jpeg",
  ".jpg": "image/jpeg", ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8", ".md": "text/markdown; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8", ".mp4": "video/mp4", ".otf": "font/otf",
  ".pdf": "application/pdf", ".png": "image/png", ".svg": "image/svg+xml",
  ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8", ".wasm": "application/wasm",
  ".webm": "video/webm", ".webp": "image/webp", ".woff": "font/woff", ".woff2": "font/woff2",
  ".xml": "application/xml; charset=utf-8",
};

export async function loadLocalEnvironment(root = projectRoot, env = process.env) {
  try {
    for (const [key, value] of Object.entries(parseEnv(await readFile(resolve(root, ".env.local"), "utf8"))))
      env[key] ??= value;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
}

export function staticHeaders(pathname, contentType) {
  const headers = new Headers({
    "Content-Type": contentType,
    "Cache-Control": pathname.startsWith("/assets/") ? "public, max-age=31536000, immutable" : "public, max-age=3600",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    // A self-hosted Space is private: none of its files belong in a search index.
    "X-Robots-Tag": "noindex, nofollow",
  });
  return headers;
}

export async function createStaticHandler(directory) {
  const root = await realpath(directory);
  return async (request) => {
    if (request.method !== "GET" && request.method !== "HEAD") return null;
    let pathname;
    try { pathname = decodeURIComponent(new URL(request.url).pathname); }
    catch { return new Response("Invalid path", { status: 400 }); }
    // Never bypass the access check by serving a similarly named public file.
    if (/^\/(?:api|app|connect|resources)(?:\/|$)/.test(pathname)) return null;
    if (pathname.includes("\0") || pathname.includes("\\") || pathname.split("/").some((part) => part.startsWith(".")))
      return new Response("Not found", { status: 404 });
    let filename, info;
    try {
      filename = await realpath(resolve(root, "." + pathname));
      if (!filename.startsWith(root + sep)) return null;
      info = await stat(filename);
    } catch (error) {
      if (["ENOENT", "ENOTDIR", "EACCES"].includes(error.code)) return null;
      throw error;
    }
    if (!info.isFile()) return null;
    const headers = staticHeaders(pathname, mimeTypes[extname(filename).toLowerCase()] ?? "application/octet-stream");
    headers.set("Content-Length", String(info.size));
    return new Response(request.method === "HEAD" ? null : Readable.toWeb(createReadStream(filename)), { headers });
  };
}

export async function startServer({ port = 3000, hostname = "127.0.0.1", root = projectRoot } = {}) {
  await loadLocalEnvironment(root);
  const assets = await createStaticHandler(resolve(root, "dist/client"));
  const { default: application } = await import(pathToFileURL(resolve(root, "dist/server/server.js")).href);
  const shown = hostname.includes(":") ? `[${hostname}]` : hostname;
  return serve({
    hostname, port,
    // The peer address lets unlock attempts be counted per client (lib/access.ts).
    fetch: async (request, { incoming }) => await assets(request) ?? application.fetch(request, {
      runtime: { env: process.env, clientAddress: incoming?.socket?.remoteAddress },
    }),
  }, (address) => console.log(`Space listening on http://${shown}:${address.port}`));
}

// Parameters: command-line flags and the environment.
// What it does: listens on HOST and PORT, which flags override. The default host accepts
// connections from this machine only; a container sets HOST=0.0.0.0.
// Output: the validated hostname and port.
export function serverOptions(args = [], env = process.env) {
  const options = { hostname: env.HOST || "127.0.0.1", port: Number(env.PORT || 3000) };
  for (let index = 0; index < args.length; index++) {
    const name = args[index];
    if (["--port", "-p"].includes(name)) options.port = Number(args[++index]);
    else if (["--hostname", "--host"].includes(name)) options.hostname = args[++index];
    else throw new Error(`Unknown server option: ${name}`);
  }
  if (!Number.isInteger(options.port) || options.port < 0 || options.port > 65535 || !options.hostname)
    throw new Error("A valid port and hostname are required");
  return options;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  // HOST and PORT may come from .env.local too, so it is read before they are.
  await loadLocalEnvironment();
  const server = await startServer(serverOptions(process.argv.slice(2)));
  for (const signal of ["SIGTERM", "SIGINT"]) process.once(signal, () => {
    server.close();
    server.closeIdleConnections?.();
  });
}
