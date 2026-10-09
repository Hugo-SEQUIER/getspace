import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';
import { gateway } from 'ai';

// The real stack: the production website build with its real API proxy and the real Python
// backend in key mode on a disposable TLS PostgreSQL (tests/stack/start.mjs). Nothing is
// mocked in the browser, so these tests cross the unlock gate, the proxy allowlist, the
// backend contract and the database. Requirements: Docker with pgvector/pgvector:pg17
// pulled, the backend virtualenv (uv sync) and a website build (npm run build).
// Run: npx e2e run --config e2e.stack.config.ts
export default {
  tests: 'tests/stack/**/*.e2e.ts',
  timeout: 90_000,
  agents: {
    default: {
      model: gateway('openai/gpt-6-luna-fast'),
      system: 'You are a thorough QA agent. Verify every outcome on screen.',
    },
  },
  targets: [{
    name: 'stack',
    engine: web(),
    app: {
      url: 'http://127.0.0.1:0',
      command: {
        executable: 'node',
        args: ['tests/stack/start.mjs', '--port', '{port}', '--state', '.e2e/stack.json'],
        env: { PATH: process.env.PATH ?? '', HOME: process.env.HOME ?? '' },
        log: '.e2e/logs/stack.log',
        startupTimeout: 240_000,
        shutdownTimeout: 20_000,
      },
      readyUrl: 'http://127.0.0.1:{port}/api/auth/session',
    },
  }],
} satisfies E2EConfig;
