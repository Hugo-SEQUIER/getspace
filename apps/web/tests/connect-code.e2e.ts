import { test, type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';

// Synthetic fixtures only: every /api request is answered in the browser. The flow
// under test is the owner's side of a device-code connection, which never redirects.
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
const account = {
  ...space,
  user: { id: 'owner', email: null },
  profile: { displayName: 'Owner', avatarUrl: null },
  access_mode: 'key',
};
const identity = { id: '88888888-8888-4888-8888-888888888888', name: 'Cursor Agent', harness: 'cursor', revision: 1 };
// The fake website's access key: a fixture value, not a credential.
const accessKey = 'connect-fixture-access-key-0123456789';

async function fakeApi(browser: Browser, log: string[], access = { unlocked: true }) {
  await browser.route('**/api/**', async (route) => {
    const url = new URL(route.request.url);
    const path = url.pathname.slice(5);
    const method = route.request.method;
    const body = route.request.postData ? JSON.parse(route.request.postData) : null;
    const json = (value: unknown) => route.fulfill({ status: 200, body: JSON.stringify(value), contentType: 'application/json' });
    if (path === 'auth/unlock' && method === 'POST') {
      log.push('unlock');
      if (body.key !== accessKey) return route.fulfill({ status: 401, json: { error: 'That access key is not correct.' } });
      access.unlocked = true;
      return json({ mode: 'key', unlocked: true });
    }
    if (!access.unlocked) return route.fulfill({ status: 401, json: { error: 'This Space is locked. Enter the access key to continue.' } });
    if (path === 'workspace' && method === 'POST') return json(account);
    if (path === 'workspaces') return json({ items: [space] });
    if (path === 'device/claim' && method === 'POST') {
      log.push(`claim ${body.user_code}`);
      if (body.user_code !== '483920')
        return route.fulfill({ status: 404, json: { error: 'That code is not valid or has expired. Ask your agent for a new one.' } });
      return json({
        authorization_id: '77777777-7777-4777-8777-777777777777',
        client: { id: 'device-code', name: 'Cursor Agent' },
        harness: 'cursor',
        redirect_uri: null,
        scope: '',
      });
    }
    if (path.startsWith('agent-identities') && method === 'GET') return json({ items: [] });
    if (path.startsWith('agent-identities') && method === 'POST') return json(identity);
    if (path === 'device/consent' && method === 'POST') {
      log.push(`consent ${body.action} write=${body.can_write} agent=${body.agent_id}`);
      return json({ status: body.action === 'approve' ? 'approved' : 'denied' });
    }
    if (method === 'GET') return json({ items: [], next_cursor: null });
    return route.fulfill({ status: 404, json: { error: 'Unexpected fixture request' } });
  });
}

test('a six-digit code connects an agent without any redirect', async ({ app, screen, browser }) => {
  const log: string[] = [];
  await fakeApi(browser, log);
  await app.open('/connect');
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Enter your agent’s code');
  const code = screen.getByLabel('Connection code');
  await expect(code).toBeEnabled();
  await expect(screen.getByRole('button', { name: 'Continue' })).toBeDisabled();
  await code.fill('483920');
  await expect(code).toHaveValue('483 920');
  await screen.getByRole('button', { name: 'Continue' }).click();
  await expect(screen.getByRole('heading', { level: 1 })).toContainText('Connect Cursor Agent');
  await expect(screen.getByText('Your agent is waiting for your decision. Nothing opens or redirects after this step.')).toBeVisible();
  await screen.getByRole('button', { name: 'Allow access' }).click();
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Agent setup');
  await expect(screen.getByText('Access approved')).toBeVisible();
  await expect(screen.getByText(/collects the connection on its own/)).toBeVisible();
  await expect(screen.getByRole('button', { name: /Return to/ })).toHaveCount(0);
  await expect(browser).toHaveURL('/connect/done');
  // The dev server's StrictMode may run the claim effect twice; the decision is sent once.
  expect(log[0]).toBe('claim 483920');
  expect(log.filter((entry) => entry.startsWith('consent'))).toEqual([
    `consent approve write=false agent=${identity.id}`,
  ]);
});

test('a wrong code explains itself and lets the person try another', async ({ app, screen, browser }) => {
  const log: string[] = [];
  await fakeApi(browser, log);
  await app.open('/connect?code=111111');
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('That code didn’t work');
  await expect(screen.getByText(/Ask your agent for a new code/)).toBeVisible();
  await screen.getByRole('button', { name: 'Enter another code' }).click();
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Enter your agent’s code');
  await expect(screen.getByLabel('Connection code')).toHaveValue('');
  expect([...new Set(log)]).toEqual(['claim 111111']);
});

test('a locked browser unlocks the Space before it reviews the code', async ({ app, screen, browser }) => {
  const log: string[] = [];
  await fakeApi(browser, log, { unlocked: false });
  await app.open('/connect?code=483920');
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();
  await expect(screen.getByText('Unlock this Space to choose what your agent can access.')).toBeVisible();
  await screen.getByLabel('Access key').fill(accessKey);
  await screen.getByRole('button', 'Unlock').tap();
  await expect(screen.getByRole('heading', { level: 1 })).toContainText('Connect Cursor Agent');
  expect(log.filter((entry) => entry === 'unlock')).toHaveLength(1);
  expect(log).toContain('claim 483920');
});
