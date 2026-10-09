// The real stack for browser tests: the production website build served by
// scripts/serve.mjs, the real website API proxy and the real Python backend in key mode on
// a disposable TLS PostgreSQL. The access key is random per run and recorded in the state
// file, so tests unlock the website exactly as an owner does.
//
//   node tests/stack/start.mjs --port <web port> [--state .e2e/stack.json]
//
// It prints the web origin when everything answers, writes the origins to the state
// file for tests to read, and tears everything down on exit or on a signal. Run it
// from apps/web. The e2e.stack.config.ts target starts it with a free port.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { randomBytes } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";

const webDirectory = resolve(new URL("../..", import.meta.url).pathname);
const backendDirectory = resolve(webDirectory, "../../backend");
const python = resolve(backendDirectory, ".venv/bin/python");
const args = process.argv.slice(2);
const option = (name, fallback) => { const index = args.indexOf(name); return index >= 0 ? args[index + 1] : fallback; };
const webPort = Number(option("--port", process.env.PORT ?? "3100"));
const stateFile = resolve(webDirectory, option("--state", ".e2e/stack.json"));
const databaseState = stateFile.replace(/\.json$/, ".database.json");
mkdirSync(resolve(stateFile, ".."), { recursive: true });

const children = [];
let stopping = false;
async function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children.reverse()) { try { child.kill("SIGTERM"); } catch {} }
  await new Promise(done => spawn(python, [resolve(backendDirectory, "tests/stack_database.py"), "stop", "--state", databaseState], { stdio: "ignore" }).on("exit", done));
  rmSync(stateFile, { force: true });
  process.exit(code);
}
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(signal, () => void shutdown(0));

function run(command, commandArgs, env, cwd) {
  const child = spawn(command, commandArgs, { cwd, env, stdio: ["ignore", "inherit", "inherit"] });
  children.push(child);
  child.on("exit", code => { if (!stopping) { console.error(`${command} exited with ${code}`); void shutdown(1); } });
  return child;
}

async function ready(url, label, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { const response = await fetch(url); if (response.status < 500) return; } catch {}
    await delay(250);
  }
  throw new Error(`${label} did not become ready at ${url}`);
}

function freePort() {
  return new Promise((done, fail) => {
    import("node:net").then(({ createServer }) => {
      const server = createServer();
      server.listen(0, "127.0.0.1", () => { const { port } = server.address(); server.close(() => done(port)); });
      server.on("error", fail);
    });
  });
}

try {
  if (!existsSync(resolve(webDirectory, "dist/server/server.js")))
    throw new Error("Build the website first: npm run build (the stack serves dist/)");
  if (!existsSync(python)) throw new Error("Install the backend first: uv sync --directory backend --locked --group dev");

  // 1. Disposable TLS PostgreSQL with the Space foundation applied.
  const database = await new Promise((done, fail) => {
    let output = "";
    const child = spawn(python, [resolve(backendDirectory, "tests/stack_database.py"), "start", "--state", databaseState], { cwd: backendDirectory, stdio: ["ignore", "pipe", "inherit"] });
    child.stdout.on("data", chunk => { output += chunk; });
    child.on("exit", code => code === 0 ? done(JSON.parse(readFileSync(databaseState, "utf8"))) : fail(new Error("database bootstrap failed")));
  });

  // 2. The real backend, in key mode.
  const accessKey = randomBytes(32).toString("hex");
  const apiPort = await freePort();
  const apiOrigin = `http://127.0.0.1:${apiPort}`;
  const webOrigin = `http://127.0.0.1:${webPort}`;
  const backendEnv = {
    PATH: process.env.PATH, HOME: process.env.HOME,
    SPACE_ENVIRONMENT: "test",
    SPACE_DATABASE_URL: database.database_url,
    SPACE_DATABASE_SSLMODE: "verify-full",
    SPACE_DATABASE_CA_FILE: database.ca_file,
    SPACE_ACCESS_MODE: "key",
    SPACE_ACCESS_KEY: accessKey,
    SPACE_WEB_URL: webOrigin,
    SPACE_MCP_RESOURCE_URL: `${apiOrigin}/mcp/`,
    SPACE_API_CORS_ORIGINS: JSON.stringify([webOrigin]),
  };
  run(python, ["-m", "uvicorn", "space.app:create_app", "--factory", "--host", "127.0.0.1", "--port", String(apiPort), "--no-access-log"], backendEnv, backendDirectory);
  await ready(`${apiOrigin}/health/ready`, "backend");

  // 3. The production website build with the real proxy.
  run(process.execPath, [resolve(webDirectory, "scripts/serve.mjs"), "--port", String(webPort)], {
    PATH: process.env.PATH, HOME: process.env.HOME,
    SPACE_WEB_URL: webOrigin,
    SPACE_API_URL: apiOrigin,
    SPACE_ACCESS_MODE: "key",
    SPACE_ACCESS_KEY: accessKey,
    SPACE_OWNER_NAME: "Stack Owner",
  }, webDirectory);
  await ready(`${webOrigin}/api/auth/session`, "website");

  writeFileSync(stateFile, JSON.stringify({ web: webOrigin, api: apiOrigin, accessKey }, null, 2));
  console.log(`stack ready: web ${webOrigin} api ${apiOrigin}`);
} catch (error) {
  console.error(error.message);
  await shutdown(1);
}
