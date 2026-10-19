import { type Browser } from '@e2e-dev/web';
import { expect } from 'e2e';
import { createDatabase } from '../lib/space/databases.ts';
import { test, type Item, type SpaceMock } from './support/space-api.ts';

// A table sorted by "Last edited", answered by the fake API. Synthetic rows only.
const databaseId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb02';
const leads = createDatabase('blank', 'Order leads', 'Synthetic records only.', databaseId);
leads.fields = [{ id: 'order-note', name: 'Note', type: 'text' }];
leads.records = ['Alpha', 'Bravo', 'Charlie'].map((name, index) => ({
  id: name.toLowerCase(), name, notes: '', values: { 'order-note': null },
  createdAt: '2026-09-01T09:00:00Z', updatedAt: `2026-09-0${3 - index}T09:00:00Z`,
}));

const rowOrder = (browser: Browser) => browser.evaluate(() =>
  [...document.querySelectorAll('.sp-db-table tbody tr')].map((row) => row.getAttribute('data-record-id')));
const focused = (browser: Browser) => browser.evaluate(() => document.activeElement?.getAttribute('aria-label') ?? null);
const stored = (space: SpaceMock) =>
  (space.databases.find(item => item.id === databaseId) as unknown as typeof leads).records;

test('a saved cell keeps its row in place and keeps focus', async ({ app, screen, browser, space }) => {
  space.databases.push({ ...leads, kind: 'database', revision: 1 } as unknown as Item);
  await app.open(`/app/databases/${databaseId}`);
  await expect(screen.getByLabel('Sort records')).toHaveValue('updated');
  expect(await rowOrder(browser)).toEqual(['alpha', 'bravo', 'charlie']);

  // Editing the last row makes it the most recently edited, but it stays where it was.
  await screen.getByRole('button', 'Edit Note for Charlie').tap();
  await screen.getByLabel('Note for Charlie').fill('Called back');
  await screen.getByLabel('Note for Charlie').press('Enter');
  await expect(screen.getByRole('button', 'Edit Note for Charlie')).toContainText('Called back');
  await expect.poll(() => focused(browser)).toBe('Edit Note for Charlie');
  expect(await rowOrder(browser)).toEqual(['alpha', 'bravo', 'charlie']);
  await expect.poll(() => stored(space).find((record) => record.id === 'charlie')?.values['order-note']).toBe('Called back');

  // Reopening the database applies the sort again.
  await browser.goto(`/app/databases/${databaseId}`);
  await expect(screen.getByRole('button', 'Edit Note for Charlie')).toContainText('Called back');
  expect(await rowOrder(browser)).toEqual(['charlie', 'alpha', 'bravo']);
});
