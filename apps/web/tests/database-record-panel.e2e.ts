import { expect } from 'e2e';
import type { Browser } from '@e2e-dev/web';
import { createDatabase } from '../lib/space/databases.ts';
import { test, type Item, type SpaceMock } from './support/space-api.ts';

// A task-like database answered by the fake API: a long name, long text values, a long
// select option, an empty property, and its own Notes column beside the record notes.
const databaseId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const database = createDatabase('blank', 'Consulting tasks', 'Synthetic records only.', databaseId);
const longName = 'Decide who owns the benchmark programme and how the steering group signs off each phase';
const longSource = 'Lead plans roadmap, open decisions list, and the notes from the second steering meeting in September';
database.fields = [
  { id: 'workstream', name: 'Workstream', type: 'select', options: ['Governance and decision rights', 'Delivery'] },
  { id: 'owner', name: 'Owner', type: 'text' },
  { id: 'blocker', name: 'Blocker', type: 'text' },
  { id: 'source', name: 'Source', type: 'text' },
  { id: 'notes', name: 'Notes', type: 'text' },
];
database.records = [{
  id: 'task-1', name: longName, notes: '',
  values: { workstream: 'Governance and decision rights', owner: 'Hugo', blocker: null, source: longSource, notes: 'Who owns the benchmark?' },
  createdAt: '2026-09-28T09:00:00Z', updatedAt: '2026-09-30T09:00:00Z',
}];

const stored = (space: SpaceMock) =>
  (space.databases.find(item => item.id === databaseId) as unknown as typeof database).records[0];
// True when a textarea shows all of its text without an inner scroll.
const fits = (browser: Browser, label: string) => browser.evaluate((label) => {
  const element = document.querySelector<HTMLTextAreaElement>(`.sp-db-record-panel textarea[aria-label="${label}"]`)!;
  return element.scrollHeight <= element.clientHeight + 1 && element.clientHeight > 40;
}, label);

test('the record panel shows a task in full and expands to a page', async ({ app, screen, browser, space }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
  space.databases.push({ ...database, kind: 'database', revision: 1 } as unknown as Item);
  await app.open(`/app/databases/${databaseId}`);
  await screen.getByRole('button', longName).tap();

  const panel = screen.getByRole('complementary', 'Record details');
  await expect(panel.getByLabel('Record name')).toHaveValue(longName);
  // The name and long text values wrap onto more lines instead of being cut off.
  expect(await fits(browser, 'Record name')).toBe(true);
  expect(await fits(browser, 'Source')).toBe(true);
  await expect(panel.getByText('Created Sep 28, 2026 · Edited Sep 30, 2026')).toBeVisible();
  await expect(panel.getByLabel('Blocker')).toHaveAttribute('placeholder', 'Empty');
  await expect(panel.getByLabel('Workstream')).toHaveValue('Governance and decision rights');
  // The record's own notes are named apart from the database's Notes property.
  await expect(panel.getByLabel('Notes')).toHaveValue('Who owns the benchmark?');
  await expect(panel.getByRole('textbox', 'Record notes')).toHaveValue('');

  // Expanding hides the database and gives the record the page width.
  const table = browser.locator('.sp-db-table');
  await panel.getByRole('button', 'Expand record').tap();
  await expect(panel.getByRole('button', 'Expand record')).toHaveAttribute('aria-pressed', 'true');
  await expect(table).toBeHidden();
  expect((await panel.getByLabel('Record name').boundingBox())!.width).toBeGreaterThan(700);

  // Shift+Enter adds a line to a text property; Enter saves the record.
  const owner = panel.getByLabel('Owner');
  await owner.fill('Hugo');
  await owner.press('End');
  await owner.press('Shift+Enter');
  await owner.pressSequentially('Steering group');
  await owner.press('Enter');
  await expect(panel.getByText('Saved record')).toBeVisible();
  expect(stored(space).values.owner).toBe('Hugo\nSteering group');
  expect(stored(space).name).toBe(longName);
  expect(space.itemCommands.at(-1)).toMatchObject({ item_id: databaseId, kind: 'database', expected_revision: 1 });

  await panel.getByRole('button', 'Expand record').tap();
  await expect(table).toBeVisible();
  await expect(panel.getByRole('button', 'Expand record')).toHaveAttribute('aria-pressed', 'false');
});
