import { lookup } from 'node:dns/promises';
import { test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { unique } from './support/stack.ts';

// A database is created, receives records, and shows them again after a reload; the
// whole record set is one encrypted workspace item on the backend.
test('a database keeps the records added to it across a reload', { session: 'owner' }, async ({ app, screen, browser }) => {
  const name = unique('Stack base');
  await app.open('/app/databases');
  await screen.getByRole('button', 'New database').tap();
  await screen.getByLabel('Database name').fill(name);
  await screen.getByRole('button', 'Create database').tap();
  await expect(browser).toHaveURL(/\/app\/databases\/[0-9a-f-]{36}$/);
  await expect(screen.getByRole('heading', name)).toBeVisible();
  const table = screen.getByRole('table');
  await expect(table.getByRole('row')).toHaveCount(1);
  // "New record" opens an inline draft; the name commits it as a row.
  for (const record of ['First record', 'Second record']) {
    await screen.getByRole('button', 'New record').first().tap();
    await screen.getByRole('textbox', 'New record name').fill(record);
    await screen.getByRole('textbox', 'New record name').press('Enter');
    await expect(table.getByRole('row', new RegExp(record)).first()).toBeVisible();
  }
  // The header and the open draft are rows too; count the records by name.
  await expect(table.getByRole('row', /First record/)).toHaveCount(1);
  await expect(table.getByRole('row', /Second record/)).toHaveCount(1);

  const url = await browser.evaluate(() => location.pathname);
  await app.open(url);
  await expect(screen.getByRole('heading', name)).toBeVisible();
  await expect(screen.getByRole('table').getByRole('row', /First record/).first()).toBeVisible();
  await expect(screen.getByRole('table').getByRole('row', /Second record/).first()).toBeVisible();

  await app.open('/app/databases');
  await expect(screen.getByRole('link', new RegExp(name))).toBeVisible();
});

test('a webhook subscription on a database is created with a one-time signing secret', { session: 'owner' }, async ({ app, screen }) => {
  // The backend resolves the receiver and accepts only public addresses, so this needs
  // DNS; nothing is delivered because no record changes and no delivery worker runs.
  test.skip(await lookup('example.org').then(() => false, () => true), 'no DNS for the receiver host');
  const name = unique('Webhook base');
  await app.open('/app/databases');
  await screen.getByRole('button', 'New database').tap();
  await screen.getByLabel('Database name').fill(name);
  await screen.getByRole('button', 'Create database').tap();
  await expect(screen.getByRole('heading', name)).toBeVisible();

  await app.open('/app/settings/webhooks');
  await screen.getByRole('combobox', 'Database').selectOption(name);
  await screen.getByRole('textbox', 'HTTPS endpoint').fill('https://example.org/space-hook');
  await screen.getByRole('button', 'Create webhook').tap();
  const secret = screen.getByRole('region', 'One-time signing secret');
  await expect(secret.getByRole('heading', 'Save your signing secret')).toBeVisible();
  expect(await secret.getByLabel('Signing secret').inputValue()).not.toEqual('');
  await expect(screen.getByRole('listitem', `Webhook for ${name} at https://example.org/space-hook`)).toBeVisible();
});
