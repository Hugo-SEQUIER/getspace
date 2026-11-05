import { test, type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { unique } from './support/stack.ts';

// Parameters: the signed-in page and the agent's name.
// What it does: creates an agent identity through the website proxy, as the site's API
// client does. The Agents page has no create form (an agent is named when its connection
// is approved), so the identity has no connection yet.
// Output: none.
async function createAgent(browser: Browser, name: string) {
  const status = await browser.evaluate(async (body: string) => (await fetch('/api/agent-identities', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
  })).status, JSON.stringify({ name, harness: 'codex' }));
  expect(status, 'the backend must accept the new identity').toBe(200);
}

// Agent identities are listed, renamed and read back through the real backend.
test('an agent identity is listed, renamed and survives a reload', { session: 'owner' }, async ({ app, screen, browser }) => {
  const name = unique('Research assistant');
  const renamed = unique('Review assistant');
  await app.open('/app/agents');
  await createAgent(browser, name);

  await app.open('/app/agents');
  await screen.getByRole('button', new RegExp(`^${name} `)).tap();
  await expect(screen.getByRole('heading', name)).toBeVisible();
  await screen.getByRole('button', 'Edit').tap();
  const dialog = screen.getByRole('dialog', 'Edit agent');
  await dialog.getByLabel('Name').fill(renamed);
  await dialog.getByRole('button', 'Save agent').tap();
  // The renamed identity stays open in the detail pane and replaces the old row.
  await expect(screen.getByRole('heading', renamed)).toBeVisible();
  await expect(screen.getByRole('button', new RegExp(`^${name} `))).toHaveCount(0);

  await app.open('/app/agents');
  await expect(screen.getByRole('button', new RegExp(`^${renamed} `))).toBeVisible();
  await expect(screen.getByRole('button', new RegExp(`^${name} `))).toHaveCount(0);
});
