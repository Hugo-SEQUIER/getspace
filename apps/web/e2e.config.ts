import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';
import { gateway } from 'ai';

// Tests fake every /api request in the browser, so no backend, account or hosted
// data is used. Set APP_URL to test a server you started yourself.
const url = process.env.APP_URL;

export default {
  // The real-stack suite under tests/stack has its own config (e2e.stack.config.ts).
  tests: ['tests/**/*.e2e.ts', '!tests/stack/**'],
  // Only agent.* steps use the model; every test here uses exact locators instead.
  agents: {
    default: {
      model: gateway('openai/gpt-6-luna-fast'),
      system: 'You are a thorough QA agent. Verify every outcome.',
    },
  },
  targets: [{
    engine: web(),
    app: url ? { url } : {
      url: 'http://127.0.0.1:0',
      command: {
        executable: 'npx',
        args: ['vite', '--host', '127.0.0.1', '--port', '{port}', '--strictPort'],
        log: '.e2e/logs/app.log',
        startupTimeout: 120_000,
      },
    },
  }],
} satisfies E2EConfig;
