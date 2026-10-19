import { expect } from 'e2e';
import { createDatabase } from '../lib/space/databases.ts';
import { test, type Item, type SpaceMock } from './support/space-api.ts';

// A small table answered by the fake API. Synthetic rows only.
const databaseId = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbb05';
const leads = createDatabase('blank', 'Selection leads', 'Synthetic records only.', databaseId);
leads.fields = [
  { id: 'selection-note', name: 'Note', type: 'text' },
  { id: 'selection-reviewed', name: 'Reviewed', type: 'checkbox' },
];
leads.records = ['Alpha', 'Bravo', 'Charlie'].map((name) => ({
  id: name.toLowerCase(), name, notes: '', values: { 'selection-note': null, 'selection-reviewed': false },
  createdAt: '2026-09-01T09:00:00Z', updatedAt: '2026-09-01T09:00:00Z',
}));

const stored = (space: SpaceMock) =>
  JSON.stringify((space.databases.find(item => item.id === databaseId) as unknown as typeof leads).records);

test('once a record is selected, clicking a row selects or clears it', async ({ app, screen, browser, space }) => {
  space.databases.push({ ...leads, kind: 'database', revision: 1 } as unknown as Item);
  await app.open(`/app/databases/${databaseId}`);
  const bar = screen.getByRole('group', 'Selected record actions');
  const panel = screen.getByRole('complementary', 'Record details');
  await expect(screen.getByRole('button', 'Edit Note for Alpha')).toBeVisible();
  const before = stored(space);

  // With nothing selected, a click on a cell edits it.
  await screen.getByRole('button', 'Edit Note for Alpha').tap();
  await expect(screen.getByLabel('Note for Alpha')).toBeVisible();
  await screen.getByLabel('Note for Alpha').press('Escape');
  await expect(bar).toHaveCount(0);

  // The whole checkbox cell selects its record, not only the box.
  await browser.locator('tr[data-record-id="alpha"] .sp-db-select-target').tap({ position: { x: 4, y: 8 } });
  await expect(screen.getByRole('checkbox', 'Select Alpha')).toBeChecked();
  await expect(bar).toContainText('1 selected');

  // Now a click on a name or a cell selects that row instead of opening or editing it.
  await screen.getByRole('button', 'Bravo').tap();
  await screen.getByRole('button', 'Edit Note for Charlie').tap();
  await expect(bar).toContainText('3 selected');
  await expect(screen.getByRole('checkbox', 'Select Charlie')).toBeChecked();
  await expect(panel).toHaveCount(0);
  await expect(screen.getByLabel('Note for Charlie')).toHaveCount(0);

  // A click on a checkbox property clears that row and leaves the property alone.
  await screen.getByRole('checkbox', 'Reviewed for Alpha').tap();
  await expect(bar).toContainText('2 selected');
  await expect(screen.getByRole('checkbox', 'Select Alpha')).not.toBeChecked();
  await expect(screen.getByRole('checkbox', 'Reviewed for Alpha')).not.toBeChecked();

  // The row menu and the keyboard keep their own actions.
  await screen.getByRole('button', 'Actions for Bravo').tap();
  await expect(screen.getByRole('menu', 'Actions for Bravo')).toBeVisible();
  await browser.keyboard.press('Escape');
  await expect(screen.getByRole('menu', 'Actions for Bravo')).toBeHidden();
  await screen.getByRole('button', 'Edit Note for Bravo').press('Enter');
  await expect(screen.getByLabel('Note for Bravo')).toBeVisible();
  await screen.getByLabel('Note for Bravo').press('Escape');
  await expect(bar).toContainText('2 selected');

  // Clearing the last selected row gives clicks back to editing and opening.
  await screen.getByRole('button', 'Bravo').tap();
  await screen.getByRole('button', 'Charlie').tap();
  await expect(bar).toHaveCount(0);
  await screen.getByRole('button', 'Edit Note for Charlie').tap();
  await expect(screen.getByLabel('Note for Charlie')).toBeVisible();
  await screen.getByLabel('Note for Charlie').press('Escape');
  await screen.getByRole('button', 'Charlie').tap();
  await expect(panel.getByLabel('Record name')).toHaveValue('Charlie');

  // Selecting changed no record.
  expect(stored(space)).toBe(before);
});
