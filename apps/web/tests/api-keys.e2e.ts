import { test, type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';

// Synthetic fixtures only: every /api request is answered in the browser, and the
// token below is a placeholder, not a credential.
const space = {
  id: '11111111-1111-4111-8111-111111111111',
  organization_id: '22222222-2222-4222-8222-222222222222',
  name: 'Private Space',
  kind: 'personal',
  role: 'owner',
  visibility: 'private',
  is_default: true,
  mcp_url: 'https://api.example.invalid/mcp/',
};
const keysPath = `spaces/${space.id}/api-keys`;
const token = 'spk_FixtureToken0000000000000000';

type ApiKey = { id: string; name: string; prefix: string; can_write: boolean; created_at: string; expires_at: string | null; last_used_at: string | null };
type Fixture = { role?: string; keys: ApiKey[]; created: unknown[]; revoked: string[] };

async function fakeApi(browser: Browser, fixture: Fixture) {
  const current = { ...space, role: fixture.role ?? space.role };
  await browser.route('**/api/**', async (route) => {
    const url = new URL(route.request.url);
    const path = url.pathname.slice(5);
    const method = route.request.method;
    const json = (value: unknown) => route.fulfill({ status: 200, body: JSON.stringify(value), contentType: 'application/json' });
    if (path === 'workspace')
      return json({ ...current, user: { id: 'owner', email: null }, profile: { displayName: 'Owner', avatarUrl: null }, access_mode: 'key' });
    if (path === 'workspaces') return json({ items: [current] });
    if (path === keysPath && method === 'GET') return json({ items: fixture.keys });
    if (path === keysPath && method === 'POST') {
      const body = JSON.parse(route.request.postData ?? '{}');
      fixture.created.push(body);
      const key = {
        id: '33333333-3333-4333-8333-333333333333', name: body.name, prefix: token.slice(0, 12),
        can_write: body.can_write, created_at: new Date().toISOString(),
        expires_at: body.expires_in_days === null ? null : new Date(Date.now() + body.expires_in_days * 86400000).toISOString(),
        last_used_at: null,
      };
      fixture.keys = [key, ...fixture.keys];
      return json({ ...key, token });
    }
    if (path.startsWith(`${keysPath}/`) && method === 'DELETE') {
      const id = path.slice(keysPath.length + 1);
      fixture.revoked.push(id);
      fixture.keys = fixture.keys.filter((key) => key.id !== id);
      return json({ revoked: true });
    }
    if (method === 'GET') return json({ items: [], next_cursor: null });
    return route.fulfill({ status: 404, json: { error: 'Unexpected fixture request' } });
  });
}

test('a person creates an API key, copies it once and revokes it', async ({ app, screen, browser }) => {
  const fixture: Fixture = { keys: [], created: [], revoked: [] };
  await fakeApi(browser, fixture);
  // Record what the page copies instead of touching the real clipboard.
  await browser.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text: string) => { (window as unknown as { copied: string }).copied = text; } },
    });
  });
  await app.open('/app/settings');
  await screen.getByRole('navigation', 'Settings').getByRole('link', 'API keys').tap();
  await expect(browser).toHaveURL('/app/settings/api-keys');
  await expect(screen.getByRole('heading', 'API keys', { level: 1 })).toBeVisible();
  await expect(screen.getByText('No API keys yet.')).toBeVisible();
  await expect(screen.getByRole('link', 'Read the documentation')).toHaveAttribute('href', 'https://github.com/Hugo-SEQUIER/getspace/tree/main/docs');
  await expect(screen.getByText('https://api.example.invalid', { exact: true })).toBeVisible();

  const create = screen.getByRole('button', 'Create key');
  await expect(create).toBeDisabled();
  await screen.getByLabel('Name').fill('Reading dashboard');
  await expect(screen.getByRole('radio', /^Read-only/)).toBeChecked();
  await screen.getByRole('radio', /^Read and write/).check();
  await screen.getByLabel('Expires').selectOption('1 year');
  await create.tap();

  const reveal = screen.getByRole('region', 'New API key');
  await expect(reveal.getByRole('heading', 'Copy your new key')).toBeVisible();
  await expect(reveal.getByRole('textbox', 'API key')).toHaveValue(token);
  await expect(reveal).toContainText('You won’t see it again');
  expect(fixture.created).toEqual([{ name: 'Reading dashboard', can_write: true, expires_in_days: 365 }]);
  await reveal.getByRole('button', 'Copy key').tap();
  await expect.poll(() => browser.evaluate(() => (window as unknown as { copied?: string }).copied ?? null)).toBe(token);

  const key = screen.getByRole('listitem', 'API key Reading dashboard');
  await expect(key).toContainText('spk_FixtureT…');
  await expect(key).toContainText('Read and write');
  await expect(key).toContainText('Never used');
  await expect(screen.getByText('No API keys yet.')).toHaveCount(0);
  await expect(screen.getByLabel('Name')).toHaveValue('');

  // The token is shown only until the person dismisses it.
  await reveal.getByRole('button', 'I’ve saved it').tap();
  await expect(screen.getByRole('region', 'New API key')).toHaveCount(0);
  await expect(screen.getByText(token)).toHaveCount(0);

  await key.getByRole('button', 'Revoke').tap();
  const dialog = screen.getByRole('dialog', 'Revoke this API key?');
  await expect(dialog).toContainText('Reading dashboard (spk_FixtureT…) stops working on its next request.');
  await dialog.getByRole('button', 'Revoke key').tap();
  await expect(screen.getByRole('dialog')).toHaveCount(0);
  await expect(screen.getByRole('listitem', 'API key Reading dashboard')).toHaveCount(0);
  await expect(screen.getByText('No API keys yet.')).toBeVisible();
  expect(fixture.revoked).toEqual(['33333333-3333-4333-8333-333333333333']);
});

test('a viewer can only create read-only keys', async ({ app, screen, browser }) => {
  const fixture: Fixture = { role: 'viewer', keys: [], created: [], revoked: [] };
  await fakeApi(browser, fixture);
  await app.open('/app/settings/api-keys');
  await expect(screen.getByText('No API keys yet.')).toBeVisible();
  await expect(screen.getByRole('radio', /^Read and write/)).toHaveCount(0);
  await expect(screen.getByText('You can view this Space without editing it, so your keys are read-only.')).toBeVisible();
  await screen.getByLabel('Name').fill('Weekly report');
  await screen.getByRole('button', 'Create key').tap();
  await expect(screen.getByRole('region', 'New API key')).toBeVisible();
  expect(fixture.created).toEqual([{ name: 'Weekly report', can_write: false, expires_in_days: 90 }]);
});
