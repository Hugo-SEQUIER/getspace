import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { createApiKey, openHome, spaceId, stack, unique, writeAsClient } from './support/stack.ts';

// The attention feature across the whole stack: a personal API key created in the UI, a
// client writing through the backend with it (attributed as a client, not the person),
// the home page reading seen marks through the proxy, and opening an item marking it.

test('what a client writes waits on the home page until the person looks or decides', { session: 'owner' }, async ({ app, screen, browser }) => {
  const { api } = stack();
  await app.open('/app/settings/api-keys');
  const key = await createApiKey(screen, browser, unique('Stack agent'));
  const space = await spaceId(browser);
  const flagged = await writeAsClient(api, key, space, {
    kind: 'task', title: unique('Approve the outreach batch'), body: 'Twelve drafts wait.', status: 'needs_you', priority: 'High',
  });
  const report = await writeAsClient(api, key, space, {
    kind: 'document', title: unique('Daily report'), body: 'Clicks rose.',
  });
  expect(flagged.author, 'a key write is a client, not the person').toBe('agent');
  expect(report.author).toBe('agent');

  await openHome(app, screen);
  const needsYou = screen.getByRole('region', 'Needs you');
  await expect(needsYou.getByRole('link', flagged.title)).toBeVisible();
  const unseen = screen.getByRole('region', 'New since you looked');
  await expect(unseen.getByRole('link', report.title)).toBeVisible();
  await expect(unseen.getByRole('link', flagged.title)).toHaveCount(0);
  await expect(browser.locator('.sp-nav-badge')).toBeVisible();

  // Opening the report marks its current revision as seen, for this person, server-side.
  await unseen.getByRole('link', report.title).tap();
  await expect(browser).toHaveURL(new RegExp(`/app/documents/${report.id}$`));
  await expect(screen.getByText('Clicks rose.')).toBeVisible();
  await app.open('/app/');
  await expect(screen.getByRole('region', 'Needs you').getByRole('link', flagged.title)).toBeVisible();
  await expect(screen.getByRole('region', 'New since you looked').getByRole('link', report.title)).toHaveCount(0);

  // A later client revision is new again; marking all as seen clears it in one request.
  const edited = await fetch(`${api}/v1/spaces/${space}/resources`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      command_id: crypto.randomUUID(), resource_id: report.id, expected_revision: report.revision,
      kind: 'document', title: report.title, body: 'Clicks rose again.', status: 'todo',
    }),
  });
  expect(edited.status).toBe(200);
  await app.open('/app/');
  await expect(screen.getByRole('region', 'New since you looked').getByRole('link', report.title)).toBeVisible();
  await screen.getByRole('region', 'New since you looked').getByRole('button', 'Mark all as seen').tap();
  await expect(screen.getByRole('region', 'New since you looked')).toHaveCount(0);
  await app.open('/app/');
  await expect(screen.getByRole('region', 'New since you looked')).toHaveCount(0);

  // Deciding on the flagged task clears "Needs you".
  await screen.getByRole('region', 'Needs you').getByRole('link', flagged.title).tap();
  await expect(browser).toHaveURL(new RegExp(`/app/tasks/${flagged.id}$`));
  await screen.getByLabel('Task status').selectOption({ value: 'done' });
  await expect(screen.getByLabel('Task status')).toHaveValue('done');
  await app.open('/app/');
  await expect(screen.getByRole('region', 'Needs you').getByRole('link', flagged.title)).toHaveCount(0);
});

test('the proxy forwards the seen-mark routes and refuses them without a session', { session: 'owner' }, async ({ app, browser }) => {
  await app.open('/app/');
  const space = await spaceId(browser);
  const signedIn = await browser.evaluate(async id => {
    const response = await fetch(`/api/spaces/${id}/views`);
    return { status: response.status, body: await response.json() };
  }, space);
  expect(signedIn.status).toBe(200);
  expect(Array.isArray(signedIn.body.items)).toBe(true);
  const anonymous = await fetch(`${stack().web}/api/spaces/${space}/views`);
  expect(anonymous.status, 'the proxy must forward, and the backend must refuse, an anonymous read').toBe(401);
});
