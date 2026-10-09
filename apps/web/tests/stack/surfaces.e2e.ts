import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { pageErrors, watchPageErrors } from '../support/space-api.ts';

// Every signed-in route renders against the real stack without a page error. A route
// whose data read fails, whose proxy path is refused, or whose bundle throws, fails here.
const routes: [string, string][] = [
  ['/app/tasks', 'Tasks'],
  ['/app/documents', 'Documents'],
  ['/app/memory', 'Memory'],
  ['/app/databases', 'Databases'],
  ['/app/overview', 'Overview'],
  ['/app/projects', 'Projects'],
  ['/app/agents', 'Agents'],
  ['/app/settings/account', 'Account'],
  ['/app/settings/security', 'Security'],
  ['/app/settings/webhooks', 'Webhooks'],
  ['/app/settings/api-keys', 'API keys'],
  ['/app/settings/processing', 'Memory processing'],
  ['/app/settings/data', 'Data and portability'],
];

for (const [path, heading] of routes) {
  test(`${path} renders ${heading} on the real stack`, { session: 'owner', tags: ['surfaces'] }, async ({ app, screen, browser }) => {
    await watchPageErrors(browser);
    await app.open(path);
    await expect(screen.getByRole('heading', heading)).toBeVisible();
    await expect(screen.getByRole('navigation', 'Main navigation').getByRole('link', 'Home')).toBeVisible();
    expect(await pageErrors(browser)).toEqual([]);
  });
}

test('the home page greets the signed-in person and offers the quick actions', { session: 'owner', tags: ['surfaces'] }, async ({ app, screen, browser }) => {
  await watchPageErrors(browser);
  await app.open('/app/');
  await expect(screen.getByRole('heading', /^Good (morning|afternoon|evening)/)).toBeVisible();
  const actions = screen.getByRole('group', 'Quick actions');
  for (const name of ['New page', 'New task', 'Search']) await expect(actions.getByRole('button', name)).toBeVisible();
  expect(await pageErrors(browser)).toEqual([]);
});

test('a visitor without the access key meets the unlock page and the API refuses them', { tags: ['surfaces'] }, async ({ app, screen, browser }) => {
  await app.open('/app/');
  await expect(screen.getByRole('heading', 'Unlock this Space')).toBeVisible();
  await expect(screen.getByLabel('Access key')).toBeVisible();
  const anonymous = await fetch(new URL('/api/workspace', app.baseUrl), { method: 'POST', headers: { Origin: new URL(app.baseUrl!).origin, 'Content-Type': 'application/json' }, body: '{}' });
  expect(anonymous.status).toBe(401);
  await expect(browser).toHaveURL(/\/app/);
});
