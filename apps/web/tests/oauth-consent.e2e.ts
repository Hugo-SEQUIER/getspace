import { test, type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { PROXY_LOCAL, proxyAllowed, proxyPath } from '../lib/space/proxy-allowlist.ts';

// Synthetic fixtures only: every /api request is answered in the browser, and the app's
// callback is a fake origin answered here too. The flow under test is the owner's side of
// a browser sign-in (OAuth): review the request, decide, and return to the app.
const space = {
  id: '11111111-1111-4111-8111-111111111111',
  organization_id: '22222222-2222-4222-8222-222222222222',
  name: 'Private Space',
  kind: 'personal',
  role: 'owner',
  visibility: 'private',
  is_default: true,
  mcp_url: 'https://space.example.invalid/mcp/',
};
const account = {
  ...space,
  user: { id: 'owner', email: null },
  profile: { displayName: 'Owner', avatarUrl: null },
  access_mode: 'key',
};
const identity = { id: '88888888-8888-4888-8888-888888888888', name: 'Claude', harness: 'claude', revision: 1 };
const requestId = '77777777-7777-4777-8777-777777777777';
const callback = 'https://claude.example/api/mcp/auth_callback';
// The fake website's access key: a fixture value, not a credential.
const accessKey = 'oauth-fixture-access-key-0123456789';

type Fixture = { unlocked: boolean; redirect: string; expired: boolean };

async function fakeApi(browser: Browser, log: string[], fixture: Partial<Fixture> = {}) {
  const state: Fixture = { unlocked: true, redirect: callback, expired: false, ...fixture };
  await browser.route('**/api/**', async (route) => {
    const url = new URL(route.request.url);
    const path = url.pathname.slice(5);
    const method = route.request.method;
    const body = route.request.postData ? JSON.parse(route.request.postData) : null;
    const json = (value: unknown) => route.fulfill({ status: 200, body: JSON.stringify(value), contentType: 'application/json' });
    // The website's proxy forwards only allowlisted paths; anything else would be a 404 there.
    if (!PROXY_LOCAL.test(path) && !proxyAllowed(proxyPath(path), method)) log.push(`unproxied ${method} ${path}`);
    if (path === 'auth/unlock' && method === 'POST') {
      log.push('unlock');
      if (body.key !== accessKey) return route.fulfill({ status: 401, json: { error: 'That access key is not correct.' } });
      state.unlocked = true;
      return json({ mode: 'key', unlocked: true });
    }
    if (!state.unlocked) return route.fulfill({ status: 401, json: { error: 'This Space is locked. Enter the access key to continue.' } });
    if (path === 'workspace' && method === 'POST') return json(account);
    if (path === 'workspaces') return json({ items: [space] });
    if (path === 'oauth/details' && method === 'GET') {
      log.push(`details ${url.searchParams.get('authorization_id')}`);
      if (state.expired)
        return route.fulfill({ status: 404, json: { error: 'This connection request expired or was already answered. Start the connection again from your app.' } });
      return json({
        authorization_id: requestId,
        client: { id: '66666666-6666-4666-8666-666666666666', name: 'Claude', uri: null },
        redirect_uri: state.redirect,
        scope: '',
      });
    }
    if (path.startsWith('agent-identities') && method === 'GET') return json({ items: [] });
    if (path.startsWith('agent-identities') && method === 'POST') return json(identity);
    if (path === 'oauth/consent' && method === 'POST') {
      log.push(`consent ${body.authorization_id} ${body.action} write=${body.can_write} agent=${body.agent_id}`);
      const answer = body.action === 'approve' ? 'code=fixture-code' : 'error=access_denied';
      return json({ redirect_url: `${state.redirect}?${answer}&state=fixture-state` });
    }
    if (method === 'GET') return json({ items: [], next_cursor: null });
    return route.fulfill({ status: 404, json: { error: 'Unexpected fixture request' } });
  });
  // Registered last so it wins over the /api/ pattern, which the callback path also matches.
  await browser.route('https://claude.example/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Claude</title><h1>Back in Claude</h1>' }));
}

test('an app that signs in is approved and returned to with its code', async ({ app, screen, browser }) => {
  const log: string[] = [];
  await fakeApi(browser, log);
  await app.open(`/connect/authorize?authorization_id=${requestId}`);
  await expect(screen.getByRole('heading', { level: 1 })).toContainText('Connect Claude');
  await expect(screen.getByText('After you decide, you return to claude.example.')).toBeVisible();
  await expect(screen.getByText(/Space does not verify the name an app registers/)).toBeVisible();
  await screen.getByRole('checkbox', /Allow changes/).check();
  await screen.getByRole('button', 'Allow access').click();

  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Agent setup');
  await expect(browser).toHaveURL('/connect/done');
  await expect(screen.getByText('Continue to claude.example')).toBeVisible();
  await expect(screen.getByText('Read and write access')).toBeVisible();
  expect(log.filter((entry) => entry.startsWith('consent'))).toEqual([
    `consent ${requestId} approve write=true agent=${identity.id}`,
  ]);
  expect(log.filter((entry) => entry.startsWith('unproxied'))).toEqual([]);

  await screen.getByRole('button', 'Return to Claude').click();
  await expect(browser).toHaveURL(`${callback}?code=fixture-code&state=fixture-state`);
  await expect(screen.getByRole('heading', 'Back in Claude')).toBeVisible();
});

test('a locked browser unlocks first, and a refusal is returned to the app', async ({ app, screen, browser }) => {
  const log: string[] = [];
  await fakeApi(browser, log, { unlocked: false });
  await app.open(`/connect/authorize?authorization_id=${requestId}`);
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();
  await screen.getByLabel('Access key').fill(accessKey);
  await screen.getByRole('button', 'Unlock').tap();
  await expect(screen.getByRole('heading', { level: 1 })).toContainText('Connect Claude');
  expect(log).toContain(`details ${requestId}`);

  await screen.getByRole('button', 'Cancel').click();
  await expect(screen.getByText('Request declined')).toBeVisible();
  await expect(screen.getByText(/Return to Claude to close the connection flow/)).toBeVisible();
  expect(log.filter((entry) => entry.startsWith('consent'))).toEqual([
    `consent ${requestId} deny write=false agent=undefined`,
  ]);
  await screen.getByRole('button', 'Return to Claude').click();
  await expect(browser).toHaveURL(`${callback}?error=access_denied&state=fixture-state`);
});

test('a callback on the app’s own computer can be finished by hand', async ({ app, screen, browser }) => {
  const log: string[] = [];
  const local = 'http://127.0.0.1:6123/callback';
  await browser.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text: string) => { (window as unknown as { copied: string }).copied = text; } },
    });
  });
  await fakeApi(browser, log, { redirect: local });
  await app.open(`/connect/authorize?authorization_id=${requestId}`);
  await expect(screen.getByText('After you decide, you return to 127.0.0.1:6123.')).toBeVisible();
  await expect(screen.getByText(/listens on the computer it runs on/)).toBeVisible();
  await screen.getByRole('button', 'Allow access').click();

  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Agent setup');
  const link = `${local}?code=fixture-code&state=fixture-state`;
  await expect(screen.getByTestId('callback-link')).toHaveText(link);
  await screen.getByRole('button', 'Copy callback link').click();
  await expect
    .poll(() => browser.evaluate(() => (window as unknown as { copied?: string }).copied ?? null))
    .toBe(link);
});

test('a sign-in without a live request explains where to start', async ({ app, screen, browser }) => {
  const log: string[] = [];
  await fakeApi(browser, log, { expired: true });
  await app.open('/connect/authorize');
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Let’s start from your app');
  await expect(screen.getByRole('button', 'Allow access')).toHaveCount(0);
  expect(log.filter((entry) => entry.startsWith('details'))).toEqual([]);

  await app.open(`/connect/authorize?authorization_id=${requestId}`);
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('We couldn’t open this request');
  await expect(screen.getByText(/Requests expire after ten minutes/)).toBeVisible();
});
