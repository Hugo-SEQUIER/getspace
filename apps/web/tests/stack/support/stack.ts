import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import type { Browser } from '@e2e-dev/web';
import { expect, type App, type Screen } from 'e2e';

/** The origins and the access key tests/stack/start.mjs wrote when the stack came up. */
export function stack() {
  return JSON.parse(readFileSync(new URL('../../../.e2e/stack.json', import.meta.url), 'utf8')) as {
    web: string; api: string; accessKey: string;
  };
}

/** A title no earlier test could have used, so lists and searches stay unambiguous. The
 * suffix is letters only, so search tokenisation treats it as one word. */
export const unique = (label: string) =>
  `${label} ${[...randomUUID().replace(/-/g, '').slice(0, 8)].map(c => String.fromCharCode(97 + parseInt(c, 16) % 26)).join('')}`;

// Parameters: the signed-in page.
// What it does: opens the person's workspace through the website proxy, which is how
// the app itself learns the Space.
// Output: the Space id.
export async function spaceId(browser: Browser) {
  const value = await browser.evaluate(async () => {
    const response = await fetch('/api/workspace', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    const body = await response.json();
    return response.ok ? (body.id as string) : `error ${response.status}`;
  });
  expect(value).toMatch(/^[0-9a-f-]{36}$/);
  return value;
}

type ResourceDraft = {
  kind: 'document' | 'task'; title: string; body?: string; status?: string; priority?: 'High' | 'Medium' | 'Low';
};

// Parameters: the backend origin, a personal API key and the resource to save.
// What it does: writes as a client of the person (not the browser), the way an agent
// or a script does, straight against the backend REST API.
// Output: the saved resource summary.
export async function writeAsClient(api: string, key: string, space: string, draft: ResourceDraft) {
  const response = await fetch(`${api}/v1/spaces/${space}/resources`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ command_id: randomUUID(), expected_revision: 0, body: '', status: 'todo', ...draft }),
  });
  expect(response.status, 'the client write must be accepted by the backend').toBe(200);
  return (await response.json()) as { id: string; revision: number; title: string; status: string; author: string };
}

// Parameters: the settings screen.
// What it does: creates a read-and-write personal API key through the real UI and reads
// the one-time reveal.
// Output: the key value, for client writes in the same test.
export async function createApiKey(screen: Screen, browser: Browser, name: string) {
  await expect(screen.getByRole('heading', 'API keys', { level: 1 })).toBeVisible();
  await screen.getByLabel('Name').fill(name);
  await screen.getByRole('radio', /^Read and write/).check();
  await screen.getByRole('button', 'Create key').tap();
  const reveal = screen.getByRole('region', 'New API key');
  // The reveal appears once the backend has answered; a read does not wait on its own.
  await expect(reveal.getByRole('textbox', 'API key')).toBeVisible();
  const key = await reveal.getByRole('textbox', 'API key').inputValue();
  expect(key).toMatch(/^spk_/);
  await reveal.getByRole('button', 'I’ve saved it').tap();
  await expect(browser.locator(`[aria-label="API key ${name}"]`)).toBeVisible();
  return key;
}

// Parameters: the app and screen.
// What it does: opens the home page and dismisses the first-run tour when it appears,
// which it does once per account and Space until dismissed.
// Output: none; the quick actions are usable afterwards.
export async function openHome(app: App, screen: Screen) {
  await app.open('/app/');
  const tour = screen.getByRole('dialog', 'Get started with Space');
  try { await tour.waitFor({ state: 'visible', timeout: 3000 }); } catch { /* No tour this time. */ }
  if (await tour.isVisible()) await tour.getByRole('button', 'Skip for now').tap();
  await expect(screen.getByRole('dialog', 'Get started with Space')).toHaveCount(0);
  await expect(screen.getByRole('group', 'Quick actions').getByRole('button', 'New page')).toBeVisible();
}
