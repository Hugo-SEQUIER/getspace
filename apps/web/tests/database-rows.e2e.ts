import { beforeEach } from '@e2e-dev/web';
import { expect } from 'e2e';
import { databaseId, sampleDatabase, test, type DatabaseRow, type Item, type SpaceMock } from './support/space-api.ts';

// Databases against the fake API: the listing carries a record count and no rows, the rows
// load when a database opens, and an edit sends only the row it changed.
beforeEach(async ({ browser }) => {
  await browser.setViewport({ width: 1440, height: 1000 });
});

const rows: DatabaseRow['records'] = [
  { id: 'deal-1', name: 'Northwind', notes: '', values: { stage: 'New', notes: 'Call back' }, createdAt: '2026-10-01T09:00:00Z', updatedAt: '2026-10-01T09:00:00Z' },
  { id: 'deal-2', name: 'Contoso', notes: '', values: { stage: 'Review', notes: 'Send quote' }, createdAt: '2026-10-02T09:00:00Z', updatedAt: '2026-10-02T09:00:00Z' },
];

/** Seeds the fixture's Pipeline database with the rows above; the Space stores it at index 0. */
function seed(space: SpaceMock) {
  const database = sampleDatabase();
  database.records = rows;
  space.databases.push(database as unknown as Item);
}

/** The stored database as it stands now: a save replaces the fixture entry, it never edits it. */
const stored = (space: SpaceMock) => space.databases[0] as unknown as DatabaseRow;

test('a cell edit saves one row, not the whole table', async ({ app, screen, space }) => {
  seed(space);
  await app.open('/app/databases');
  const card = screen.getByRole('link', { name: /Pipeline/ });
  await expect(card).toContainText('2 records');
  // The listing answered without rows: nothing opened a database yet.
  expect(space.databaseReads).toEqual([]);

  await card.tap();
  await expect(screen.getByRole('heading', 'Pipeline')).toBeVisible();
  await expect(screen.getByRole('button', 'Edit Notes for Northwind')).toContainText('Call back');
  expect(space.databaseReads).toEqual([databaseId]);

  await screen.getByRole('button', 'Edit Notes for Northwind').tap();
  await screen.getByLabel('Notes for Northwind').fill('Called back');
  await screen.getByLabel('Notes for Northwind').press('Enter');
  await expect(screen.getByRole('button', 'Edit Notes for Northwind')).toContainText('Called back');

  await expect.poll(() => space.databaseCommands.length).toBe(1);
  const command = space.databaseCommands[0] as { expected_revision: number; data?: unknown; upsert: { id: string; values: Record<string, unknown> }[]; delete: string[] };
  expect(command.expected_revision).toBe(1);
  expect(command).not.toHaveProperty('data');
  expect(command.delete).toEqual([]);
  expect(command.upsert.map((row) => row.id)).toEqual(['deal-1']);
  expect(command.upsert[0].values).toMatchObject({ stage: 'New', notes: 'Called back' });
  // The other row never travelled, and the Space now holds the edit at revision 2.
  expect(stored(space).revision).toBe(2);
  expect(stored(space).records.map((row) => row.values.notes)).toEqual(['Called back', 'Send quote']);
  await expect(screen.getByText(/2 of 2 records/)).toBeVisible();
});

test('a schema change saves the schema alone and a deleted row sends its ID', async ({ app, screen, space }) => {
  seed(space);
  await app.open(`/app/databases/${databaseId}`);
  await expect(screen.getByRole('button', 'Edit Notes for Contoso')).toContainText('Send quote');

  await screen.getByRole('button', { name: 'Board' }).tap();
  await expect.poll(() => space.databaseCommands.length).toBe(1);
  const view = space.databaseCommands[0] as { data?: { view: string }; upsert: unknown[]; delete: string[] };
  expect(view.data?.view).toBe('board');
  expect(view.upsert).toEqual([]);
  expect(view.delete).toEqual([]);

  await screen.getByRole('button', { name: 'Table' }).tap();
  await expect.poll(() => space.databaseCommands.length).toBe(2);
  await screen.getByRole('checkbox', 'Select Contoso').check();
  await screen.getByRole('group', 'Selected record actions').getByRole('button', { name: /Delete/ }).tap();
  await screen.getByRole('dialog').getByRole('button', { name: /Delete/ }).tap();
  await expect.poll(() => space.databaseCommands.length).toBe(3);
  const removal = space.databaseCommands[2] as { data?: unknown; upsert: unknown[]; delete: string[] };
  expect(removal).not.toHaveProperty('data');
  expect(removal.upsert).toEqual([]);
  expect(removal.delete).toEqual(['deal-2']);
  expect(stored(space).records.map((row) => row.id)).toEqual(['deal-1']);
});
