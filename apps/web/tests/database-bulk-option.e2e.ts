import { expect } from 'e2e';
import { createDatabase } from '../lib/space/databases.ts';
import { test, type Item, type SpaceMock } from './support/space-api.ts';

// A small table answered by the fake API. Synthetic rows only.
const dealsId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb03';
const deals = createDatabase('blank', 'Bulk option deals', 'Synthetic records only.', dealsId);
deals.fields = [
  { id: 'bulk-stage', name: 'Stage', type: 'select', options: ['Open', 'Won', 'Lost'] },
  { id: 'bulk-priority', name: 'Priority', type: 'select', options: ['Low', 'High'] },
  { id: 'bulk-note', name: 'Note', type: 'text' },
];
deals.records = [['Alpha', 'Open'], ['Bravo', 'Open'], ['Charlie', 'Won']].map(([name, stage]) => ({
  id: name.toLowerCase(), name, notes: '', values: { 'bulk-stage': stage, 'bulk-priority': null, 'bulk-note': `${name} note` },
  createdAt: '2026-09-01T09:00:00Z', updatedAt: '2026-09-01T09:00:00Z',
}));

type Stored = { id: string; values: Record<string, unknown>; updatedAt: string };
const stored = (space: SpaceMock, id: string): Stored[] =>
  (space.databases.find(item => item.id === id) as unknown as { records: Stored[] }).records;

test('selected records take one select value together', async ({ app, screen, space }) => {
  space.databases.push({ ...deals, kind: 'database', revision: 1 } as unknown as Item);
  await app.open(`/app/databases/${dealsId}`);
  const bar = screen.getByRole('group', 'Selected record actions');
  await expect(screen.getByRole('button', 'Edit Stage for Alpha')).toHaveText('Open');

  await screen.getByRole('checkbox', 'Select Alpha').check();
  await screen.getByRole('checkbox', 'Select Bravo').check();
  await expect(bar).toContainText('2 selected');

  // The menu offers only select properties, each with its options and an empty choice.
  await bar.getByRole('button', 'Set value on selected').tap();
  const menu = screen.getByRole('menu', 'Set value on selected');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('group')).toHaveCount(2);
  await expect(menu.getByRole('group', 'Stage').getByRole('menuitem')).toHaveText(['Open', 'Won', 'Lost', 'No stage']);
  await expect(menu.getByRole('group', 'Priority').getByRole('menuitem')).toHaveText(['Low', 'High', 'No priority']);
  await expect(menu.getByRole('group', 'Note')).toHaveCount(0);

  await menu.getByRole('group', 'Stage').getByRole('menuitem', 'Won').tap();
  await expect(screen.getByRole('status').filter({ hasText: 'Set Stage to “Won” on 2 records.' })).toBeVisible();
  await expect(menu).toBeHidden();
  await expect(screen.getByRole('button', 'Edit Stage for Alpha')).toHaveText('Won');
  await expect(screen.getByRole('button', 'Edit Stage for Bravo')).toHaveText('Won');
  await expect(bar).toContainText('2 selected');
  await expect.poll(() => stored(space, dealsId).map((row) => row.values['bulk-stage'])).toEqual(['Won', 'Won', 'Won']);
  let rows = stored(space, dealsId);
  expect(rows.map((row) => row.values['bulk-note'])).toEqual(['Alpha note', 'Bravo note', 'Charlie note']);
  expect(rows[2].updatedAt).toBe('2026-09-01T09:00:00Z');
  expect(rows[0].updatedAt).not.toBe('2026-09-01T09:00:00Z');

  // The keyboard opens the menu on its first option, and the empty choice clears the property.
  await bar.getByRole('button', 'Set value on selected').press('ArrowDown');
  await expect(menu.getByRole('group', 'Stage').getByRole('menuitem', 'Open')).toBeFocused();
  await menu.getByRole('group', 'Priority').getByRole('menuitem', 'No priority').tap();
  await expect(screen.getByRole('status').filter({ hasText: 'Cleared Priority on 2 records.' })).toBeVisible();
  await bar.getByRole('button', 'Set value on selected').tap();
  await menu.getByRole('group', 'Stage').getByRole('menuitem', 'No stage').tap();
  await expect(screen.getByRole('status').filter({ hasText: 'Cleared Stage on 2 records.' })).toBeVisible();
  await expect(screen.getByRole('button', 'Edit Stage for Alpha')).toHaveText('-');
  await expect.poll(() => stored(space, dealsId).map((row) => row.values['bulk-stage'])).toEqual([null, null, 'Won']);
  rows = stored(space, dealsId);
  expect(rows[2].updatedAt).toBe('2026-09-01T09:00:00Z');
});

test('the selection bar offers no value menu without a select property', async ({ app, screen, space }) => {
  const plainId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb04';
  const plain = createDatabase('blank', 'Bulk option plain', 'Synthetic records only.', plainId);
  plain.fields = [{ id: 'plain-note', name: 'Note', type: 'text' }];
  plain.records = [{ id: 'delta', name: 'Delta', notes: '', values: { 'plain-note': null }, createdAt: '2026-09-01T09:00:00Z', updatedAt: '2026-09-01T09:00:00Z' }];
  space.databases.push({ ...plain, kind: 'database', revision: 1 } as unknown as Item);
  await app.open(`/app/databases/${plainId}`);
  await screen.getByRole('checkbox', 'Select Delta').check();
  const bar = screen.getByRole('group', 'Selected record actions');
  await expect(bar).toContainText('1 selected');
  await expect(bar.getByRole('button', 'Duplicate selected')).toBeVisible();
  await expect(bar.getByRole('button', { name: /^Set / })).toHaveCount(0);
});
