import { createHash, randomBytes } from 'node:crypto';
import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { stack, unique } from './support/stack.ts';

// An app that signs in through the browser, such as claude.ai, end to end: the backend's
// discovery documents and OAuth endpoints, the website's consent page behind the access
// key, the code exchange with PKCE, and an MCP call with the token. The app's callback is a
// fake origin answered in the browser.
const callback = 'https://claude.example/api/mcp/auth_callback';

async function mcp(api: string, token: string, name: string, args: object) {
  const response = await fetch(`${api}/mcp/`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json, text/event-stream',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name, arguments: args } }),
  });
  return { status: response.status, body: response.status === 200 ? await response.json() : null };
}

test('an app signs in through the browser and reaches MCP with its own token', { session: 'owner' }, async ({ app, screen, browser }) => {
  const { api } = stack();
  const name = unique('Claude');

  // Discovery, as a client that knows only the MCP address does it.
  const anonymous = await mcp(api, '', 'workspace_info', {});
  expect(anonymous.status).toBe(401);
  const resource = await (await fetch(`${api}/.well-known/oauth-protected-resource/mcp`)).json();
  const server = await (await fetch(new URL('.well-known/oauth-authorization-server', resource.authorization_servers[0]))).json();
  const registration = await fetch(server.registration_endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_name: name, redirect_uris: [callback], token_endpoint_auth_method: 'none' }),
  });
  expect(registration.status).toBe(201);
  const client = await registration.json();

  const verifier = randomBytes(32).toString('base64url');
  const authorize = new URL(server.authorization_endpoint);
  authorize.search = new URLSearchParams({
    response_type: 'code',
    client_id: client.client_id,
    redirect_uri: callback,
    code_challenge: createHash('sha256').update(verifier).digest('base64url'),
    code_challenge_method: 'S256',
    state: 'stack-state',
    resource: resource.resource,
  }).toString();
  const started = await fetch(authorize, { redirect: 'manual' });
  expect(started.status).toBe(302);
  const consent = new URL(started.headers.get('location') ?? '');
  expect(consent.pathname).toBe('/connect/authorize');

  // The owner reviews and approves on the website, then returns to the app.
  await browser.route('https://claude.example/**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<title>App</title><h1>Back in the app</h1>' }));
  await app.open(consent.pathname + consent.search);
  await expect(screen.getByRole('heading', { level: 1 })).toContainText(`Connect ${name}`);
  await expect(screen.getByText('After you decide, you return to claude.example.')).toBeVisible();
  await screen.getByRole('checkbox', /Allow changes/).check();
  await screen.getByRole('button', 'Allow access').click();
  await expect(screen.getByRole('heading', { level: 1 })).toHaveText('Agent setup');
  await screen.getByRole('button', `Return to ${name}`).click();
  await expect(screen.getByRole('heading', 'Back in the app')).toBeVisible();
  const returned = new URL(await browser.url());
  expect(returned.searchParams.get('state')).toBe('stack-state');

  const exchanged = await fetch(server.token_endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      code: returned.searchParams.get('code') ?? '',
      redirect_uri: callback,
      client_id: client.client_id,
      code_verifier: verifier,
      resource: resource.resource,
    }),
  });
  expect(exchanged.status).toBe(200);
  const tokens = await exchanged.json();
  expect(tokens.access_token).toMatch(/^spo_/);

  // The token reaches MCP as the agent identity chosen during consent.
  const identified = await mcp(api, tokens.access_token, 'identify_agent', {});
  expect(identified.status).toBe(200);
  expect(identified.body.result.structuredContent.agent.name).toBe(name);

  // The connection is listed under that agent on the Agents page.
  await app.open('/app/agents');
  await expect(screen.getByRole('button', new RegExp(`^${name} `))).toContainText('Connected');
});
