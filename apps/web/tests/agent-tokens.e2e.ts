import { expect } from 'e2e';
import { createdAgentId, fixtureConnectionToken, spaceId, test } from './support/space-api.ts';

// The fake API issues a synthetic connection token; nothing here reaches a real Space.
const copied = (browser: { evaluate: <T>(fn: () => T) => Promise<T> }) =>
  browser.evaluate(() => (window as unknown as { copied?: string }).copied ?? null);

test('a connection token is created on the Agents page and shown once with its setup', async ({ app, screen, browser, space }) => {
  // Record what the page copies instead of touching the real clipboard.
  await browser.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text: string) => { (window as unknown as { copied: string }).copied = text; } },
    });
  });
  space.space.mcp_url = 'https://space.example.invalid/mcp/';
  await app.open('/app/agents');
  await expect(screen.getByRole('heading', 'Agents', { level: 1 })).toBeVisible();
  await expect(screen.getByRole('heading', 'Connect your first agent')).toBeVisible();
  await screen.getByRole('button', 'Connect an agent').first().tap();

  const dialog = screen.getByRole('dialog', 'Connect an agent');
  await expect(dialog.getByRole('button', 'Create connection')).toBeDisabled();
  await dialog.getByLabel('Name').fill('Claude Code on my laptop');
  await dialog.getByRole('checkbox', /Allow changes/).check();
  await dialog.getByRole('button', 'Create connection').tap();

  const result = dialog.getByRole('region', 'New connection');
  await expect(result.getByRole('heading', 'Copy the token now')).toBeVisible();
  await expect(result.getByRole('textbox', 'Connection token')).toHaveValue(fixtureConnectionToken);
  await expect(result).toContainText('This is the only time the token is shown');
  await expect(result.getByTestId('claude-code-setup')).toHaveText(
    `claude mcp add --transport http space https://space.example.invalid/mcp/ --header "Authorization: Bearer ${fixtureConnectionToken}" --header "X-Space-Agent-ID: ${createdAgentId}"`,
  );
  await expect(result.getByTestId('codex-setup')).toContainText('[mcp_servers.space]');
  await expect(result.getByTestId('json-setup')).toContainText('"mcpServers"');
  expect(space.tokenRequests).toEqual([{
    name: 'Claude Code on my laptop', harness: 'claude-code', can_write: true, agent_id: createdAgentId, space_id: spaceId,
  }]);

  await result.getByRole('button', 'Copy token').tap();
  await expect.poll(() => copied(browser)).toBe(fixtureConnectionToken);
  await result.getByRole('button', 'Copy command').tap();
  await expect.poll(() => copied(browser)).toContain('claude mcp add --transport http space');

  // Closing forgets the token: the agent and its connection stay, the token is never shown again.
  await result.getByRole('button', 'I’ve saved it').tap();
  await expect(screen.getByRole('dialog')).toHaveCount(0);
  await expect(screen.getByText(fixtureConnectionToken, { exact: false })).toHaveCount(0);
  const agent = screen.getByRole('button', /Claude Code on my laptop/);
  await expect(agent).toContainText('Connected');

  await agent.tap();
  await screen.getByRole('button', 'Revoke this connection').tap();
  await screen.getByRole('dialog', 'Revoke this connection?').getByRole('button', 'Revoke connection').tap();
  await expect(agent).toContainText('Revoked');
  await expect(screen.getByRole('button', 'Revoke this connection')).toHaveCount(0);
});

test('an existing agent is preselected when it connects another client', async ({ app, screen, space }) => {
  space.agents = [{ id: createdAgentId, name: 'Research assistant', harness: 'codex', revision: 1 }];
  await app.open('/app/agents');
  await screen.getByRole('button', /Research assistant/).tap();
  await screen.getByRole('button', 'Create a connection').tap();
  const dialog = screen.getByRole('dialog', 'Connect an agent');
  await expect(dialog.getByLabel('Agent')).toHaveValue(createdAgentId);
  await expect(dialog.getByLabel('Name')).toHaveCount(0);
  await dialog.getByRole('button', 'Create connection').tap();
  await expect(dialog.getByRole('region', 'New connection')).toContainText('read-only access');
  expect(space.tokenRequests).toEqual([{
    name: 'Research assistant', harness: 'codex', can_write: false, agent_id: createdAgentId, space_id: spaceId,
  }]);
});

test('apps that sign in are given the connector address, and a private Space offers none', async ({ app, screen, browser, space }) => {
  await browser.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text: string) => { (window as unknown as { copied: string }).copied = text; } },
    });
  });
  space.space.mcp_url = 'https://space.example.invalid/mcp/';
  await app.open('/app/agents');
  await screen.getByRole('button', 'Connect an agent').first().tap();
  const option = screen.getByRole('dialog', 'Connect an agent').getByRole('region', 'Connect an app that signs in');
  await expect(option).toContainText('custom connector');
  await expect(option.getByTestId('connector-address')).toHaveText('https://space.example.invalid/mcp/');
  await option.getByRole('button', 'Copy address').tap();
  await expect.poll(() => copied(browser)).toBe('https://space.example.invalid/mcp/');

  // Without an access key there is no sign-in: agents reach a private Space directly.
  space.access.mode = 'private';
  await app.open('/app/agents');
  await screen.getByRole('button', 'Connect an agent').first().tap();
  await expect(screen.getByRole('dialog', 'Connect an agent').getByLabel('Name')).toBeVisible();
  await expect(screen.getByRole('region', 'Connect an app that signs in')).toHaveCount(0);
});
