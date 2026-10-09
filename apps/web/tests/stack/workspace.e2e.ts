import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { openHome, unique } from './support/stack.ts';

// Documents and tasks written through the real proxy and backend, read back after a
// reload, found by search, and moved on the board. Every title is unique, so tests
// share one Space without depending on each other.

test('a page written from the home page is saved, listed and searchable', { session: 'owner' }, async ({ app, screen, browser }) => {
  const title = unique('Stack page');
  await openHome(app, screen);
  await screen.getByRole('group', 'Quick actions').getByRole('button', 'New page').tap();
  const dialog = screen.getByRole('dialog', 'New document');
  await dialog.getByLabel('Title').fill(title);
  await dialog.getByLabel('Content').fill('Written through the real proxy and backend.');
  await dialog.getByRole('button', 'Save').tap();
  await expect(browser).toHaveURL(/\/app\/documents\/[0-9a-f-]{36}$/);
  await expect(screen.getByText('Written through the real proxy and backend.')).toBeVisible();

  await app.open('/app/documents');
  await expect(screen.getByRole('region', 'Document library').getByRole('link', title)).toBeVisible();

  await app.open('/app/search');
  const search = screen.getByRole('textbox', 'Search this space');
  // A result's accessible name carries its type and excerpt around the title.
  const result = screen.getByRole('link', new RegExp(title));
  // Other tests write to the same Space in parallel. Search pages through one snapshot and
  // asks for a restart when the Space changes under it, so search again, a bounded few times.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await search.fill('');
    await search.fill(title.split(' ').at(-1)!);
    try { await result.waitFor({ state: 'visible', timeout: 5000 }); break; } catch { /* The Space changed; search again. */ }
  }
  await expect(result).toBeVisible();
  await expect(screen.getByText('1 results in your Space')).toBeVisible();
});

test('a task moves through its statuses and the board agrees after a reload', { session: 'owner' }, async ({ app, screen, browser }) => {
  const title = unique('Stack task');
  await openHome(app, screen);
  await screen.getByRole('group', 'Quick actions').getByRole('button', 'New task').tap();
  const dialog = screen.getByRole('dialog', 'New task');
  await dialog.getByLabel('Title').fill(title);
  await dialog.getByLabel('Content').fill('Move me.');
  await dialog.getByRole('button', 'Save').tap();
  await expect(browser).toHaveURL(/\/app\/tasks\/[0-9a-f-]{36}$/);
  await screen.getByLabel('Task status').selectOption({ value: 'in_progress' });
  await expect(screen.getByLabel('Task status')).toHaveValue('in_progress');

  await app.open('/app/tasks?view=board');
  await expect(browser.locator('[data-status="in_progress"]').getByRole('link', title)).toBeVisible();
  await expect(browser.locator('[data-status="todo"]').getByRole('link', title)).toHaveCount(0);
});

test('the home page lists what was just written, in order', { session: 'owner' }, async ({ app, screen }) => {
  const first = unique('Stack order A');
  const second = unique('Stack order B');
  for (const title of [first, second]) {
    await openHome(app, screen);
    await screen.getByRole('group', 'Quick actions').getByRole('button', 'New page').tap();
    const dialog = screen.getByRole('dialog', 'New document');
    await dialog.getByLabel('Title').fill(title);
    await dialog.getByRole('button', 'Save').tap();
    await expect(screen.getByRole('dialog', 'New document')).toHaveCount(0);
  }
  await openHome(app, screen);
  const recent = screen.getByRole('region', 'Jump back in');
  await expect(recent.getByRole('link', new RegExp(`^${second}`))).toBeVisible();
  await expect(recent.getByRole('link', new RegExp(`^${first}`))).toBeVisible();
});
